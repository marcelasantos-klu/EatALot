from flask import Flask, render_template, request, redirect, url_for, flash, session
from db import get_conn

app = Flask(__name__)
app.secret_key = "eatalot-secret"


# ----------------------
# DB Helpers
# ----------------------

def q_all(sql, params=()):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params)
            return cur.fetchall()


def q_one(sql, params=()):
    rows = q_all(sql, params)
    return rows[0] if rows else None


def next_id(table, col):
    row = q_one(f"SELECT COALESCE(MAX({col}),0)+1 FROM {table};")
    return int(row[0])


def recompute_order_total(order_id: int) -> None:
    """Recalculate Order.total_amount as SUM(quantity * priceatordertime) from Order_Item."""
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(
                """
                UPDATE "Order" o
                SET total_amount = COALESCE(s.total, 0)
                FROM (
                    SELECT order_id, SUM(quantity * priceatordertime) AS total
                    FROM order_item
                    WHERE order_id=%s
                    GROUP BY order_id
                ) s
                WHERE o.order_id=%s;
                """,
                (order_id, order_id),
            )
        conn.commit()


# ----------------------
# Home (Dashboard)
# ----------------------

@app.get("/")
def home():
    stats = q_one(
        """
        SELECT
            COUNT(*) AS total_orders,
            COUNT(*) FILTER (WHERE order_status IN ('created','confirmed','preparing')) AS pending_orders,
            COUNT(*) FILTER (WHERE order_status='delivered') AS delivered_orders
        FROM "Order";
        """
    )

    recent = q_all(
        """
        SELECT
            o.order_id,
            o.order_timestamp,
            o.order_status,
            o.delivery_address,
            r.name AS restaurant_name,

            -- who created it (works for individual orders)
            COALESCE(pc.first_name || ' ' || pc.last_name, 'Group order') AS customer_name,
            pc.email_address,

            -- delivery info
            d.delivery_status,
            d.pickup_time,
            d.dropoff_time,

            -- courier name (if assigned)
            COALESCE(c.first_name || ' ' || c.last_name, '-') AS courier_name,

            -- time to deliver in minutes (order_timestamp -> dropoff_time)
            CASE
                WHEN d.dropoff_time IS NOT NULL
                THEN ROUND(EXTRACT(EPOCH FROM (d.dropoff_time - o.order_timestamp)) / 60.0, 1)
                ELSE NULL
            END AS minutes_to_deliver,

            o.total_amount
        FROM "Order" o
        JOIN restaurant r ON r.restaurant_id = o.restaurant_id
        LEFT JOIN participantcustomer pc ON pc.customer_id = o.customer_id
        LEFT JOIN delivery d ON d.order_id = o.order_id
        LEFT JOIN assigned a ON a.delivery_id = d.delivery_id
        LEFT JOIN courier c ON c.courier_id = a.courier_id
        ORDER BY o.order_timestamp DESC
        LIMIT 20;
        """
    )

    return render_template(
        "home.html",
        total=stats[0] if stats else 0,
        pending=stats[1] if stats else 0,
        delivered=stats[2] if stats else 0,
        recent=recent,
    )


# ----------------------
# Restaurants (Start Order Page)
# ----------------------

@app.get("/restaurants")
def restaurants():
    rows = q_all("SELECT restaurant_id, name, cuisine_type FROM restaurant ORDER BY restaurant_id;")
    return render_template("restaurants.html", restaurants=rows)


@app.get("/manager")
def manager_dashboard():
    """Manager view: customer + order KPIs and recent orders."""

    # Customer KPIs (based on individual orders + payments)
    customers = q_all(
        """
        SELECT
            c.customer_id,
            c.first_name,
            c.last_name,
            c.email_address,
            c.phone_number,
            COUNT(DISTINCT o.order_id) FILTER (WHERE o.customer_id = c.customer_id) AS individual_orders,
            COALESCE(SUM(pt.amount) FILTER (WHERE pt.payer_customer_id = c.customer_id), 0) AS total_spend,
            MAX(pt.transaction_time) FILTER (WHERE pt.payer_customer_id = c.customer_id) AS last_payment_time
        FROM participantcustomer c
        LEFT JOIN "Order" o ON o.customer_id = c.customer_id
        LEFT JOIN paymenttransaction pt ON pt.payer_customer_id = c.customer_id
        GROUP BY c.customer_id, c.first_name, c.last_name, c.email_address, c.phone_number
        ORDER BY total_spend DESC, individual_orders DESC, c.customer_id;
        """
    )

    # Recent orders (tries to show a customer name even for group orders)
    recent_orders = q_all(
        """
        SELECT
            o.order_id,
            o.order_timestamp,
            o.order_status,
            o.delivery_address,
            r.name AS restaurant_name,
            COALESCE(pc.first_name || ' ' || pc.last_name,
                     payer.first_name || ' ' || payer.last_name,
                     item_owner.first_name || ' ' || item_owner.last_name,
                     'Group order') AS customer_name,
            o.total_amount
        FROM "Order" o
        JOIN restaurant r ON r.restaurant_id = o.restaurant_id
        LEFT JOIN participantcustomer pc ON pc.customer_id = o.customer_id
        LEFT JOIN paymenttransaction pt ON pt.order_id = o.order_id
        LEFT JOIN participantcustomer payer ON payer.customer_id = pt.payer_customer_id
        LEFT JOIN LATERAL (
            SELECT DISTINCT oi.customer_id
            FROM order_item oi
            WHERE oi.order_id = o.order_id
            ORDER BY oi.customer_id
            LIMIT 1
        ) oi1 ON true
        LEFT JOIN participantcustomer item_owner ON item_owner.customer_id = oi1.customer_id
        ORDER BY o.order_timestamp DESC
        LIMIT 25;
        """
    )

    # Delivery status summary
    delivery_stats = q_one(
        """
        SELECT
            COUNT(*) AS deliveries,
            COUNT(*) FILTER (WHERE delivery_status='assigned') AS assigned,
            COUNT(*) FILTER (WHERE delivery_status='delivered') AS delivered
        FROM delivery;
        """
    )

    return render_template(
        "transactions.html",
        customers=customers,
        recent_orders=recent_orders,
        deliveries=delivery_stats[0] if delivery_stats else 0,
        assigned_deliveries=delivery_stats[1] if delivery_stats else 0,
        delivered_deliveries=delivery_stats[2] if delivery_stats else 0,
    )


# ----------------------
# Start Order (Individual OR Group)
# ----------------------

@app.post("/start_order")
def start_order():
    restaurant_id = request.form.get("restaurant_id")
    order_type = request.form.get("order_type", "individual")  # individual | group
    group_name = request.form.get("group_name")

    first = request.form.get("first_name")
    last = request.form.get("last_name")
    address = request.form.get("address")
    email = (request.form.get("email") or "").strip()

    # Organizer = person using UI
    customer = None

    if email:
        customer = q_one(
            "SELECT customer_id FROM participantcustomer WHERE email_address=%s LIMIT 1;",
            (email,),
        )

    if not customer:
        customer = q_one(
            "SELECT customer_id FROM participantcustomer WHERE first_name=%s AND last_name=%s LIMIT 1;",
            (first, last),
        )

    # If customer not found -> create customer (requires email)
    if not customer:
        if not email:
            flash("Customer not found. Please enter an email address to create a new customer.")
            return redirect(url_for("restaurants"))

        new_customer_id = next_id("participantcustomer", "customer_id")
        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO participantcustomer
                    (customer_id, first_name, last_name, email_address, phone_number, delivery_address, created_at)
                    VALUES (%s,%s,%s,%s,%s,%s,CURRENT_TIMESTAMP);
                    """,
                    (new_customer_id, first, last, email, "-", address),
                )
            conn.commit()

        organizer_customer_id = new_customer_id
    else:
        organizer_customer_id = int(customer[0])

    group_id = None
    if order_type == "group":
        if not group_name:
            flash("Please enter a group name for a group order.")
            return redirect(url_for("restaurants"))

        grp = q_one('SELECT group_id FROM "Group" WHERE group_name=%s LIMIT 1;', (group_name,))
        if not grp:
            flash('Group not found. Group name must match seeded "Group".group_name.')
            return redirect(url_for("restaurants"))

        group_id = int(grp[0])

        # Organizer must belong to that group (matches your Belongs + trigger logic)
        member = q_one(
            "SELECT 1 FROM belongs WHERE group_id=%s AND customer_id=%s;",
            (group_id, organizer_customer_id),
        )
        if not member:
            flash("Organizer must be a member of the selected group (Belongs table).")
            return redirect(url_for("restaurants"))

    order_id = next_id('"Order"', "order_id")

    with get_conn() as conn:
        with conn.cursor() as cur:
            if order_type == "group":
                cur.execute(
                    """
                    INSERT INTO "Order"
                    (order_id, restaurant_id, order_timestamp, order_status,
                     delivery_address, total_amount, customer_id, group_id)
                    VALUES (%s,%s,CURRENT_TIMESTAMP,'created',%s,0,NULL,%s);
                    """,
                    (order_id, restaurant_id, address, group_id),
                )
                # store organizer for later (items + payment payer)
                session[f"order_organizer_{order_id}"] = organizer_customer_id
            else:
                cur.execute(
                    """
                    INSERT INTO "Order"
                    (order_id, restaurant_id, order_timestamp, order_status,
                     delivery_address, total_amount, customer_id, group_id)
                    VALUES (%s,%s,CURRENT_TIMESTAMP,'created',%s,0,%s,NULL);
                    """,
                    (order_id, restaurant_id, address, organizer_customer_id),
                )

        conn.commit()

    return redirect(url_for("menu", order_id=order_id))


# ----------------------
# Menu (Add Items)
# ----------------------

@app.route("/menu/<int:order_id>", methods=["GET", "POST"])
def menu(order_id):
    order = q_one('SELECT restaurant_id FROM "Order" WHERE order_id=%s;', (order_id,))
    if not order:
        return "Order not found"

    restaurant_id = order[0]

    items = q_all(
        """
        SELECT mi.menuitem_id, mi.item_name, mi.price
        FROM menu m
        JOIN contains_menuitem c ON c.menu_id = m.menu_id
        JOIN menu_item mi ON mi.menuitem_id = c.menuitem_id
        WHERE m.restaurant_id=%s
        ORDER BY mi.menuitem_id;
        """,
        (restaurant_id,),
    )

    if request.method == "POST":
        menuitem_id = request.form.get("menuitem_id")
        qty = request.form.get("quantity")
        special = request.form.get("special_instructions")

        price = q_one("SELECT price FROM menu_item WHERE menuitem_id=%s;", (menuitem_id,))[0]

        with get_conn() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COALESCE(MAX(line_no),0)+1 FROM order_item WHERE order_id=%s;", (order_id,))
                line_no = cur.fetchone()[0]

                # Determine who owns the line-item (important for group order trigger)
                ord_ctx = q_one('SELECT customer_id, group_id FROM "Order" WHERE order_id=%s;', (order_id,))
                order_customer_id, order_group_id = ord_ctx

                if order_group_id is not None:
                    item_customer_id = session.get(f"order_organizer_{order_id}")
                    if item_customer_id is None:
                        flash("Session expired for group order organizer. Please start the order again.")
                        return redirect(url_for("restaurants"))
                else:
                    item_customer_id = order_customer_id

                cur.execute(
                    """
                    INSERT INTO order_item
                    (order_id, line_no, menuitem_id, quantity,
                     priceatordertime, special_instructions, customer_id)
                    VALUES (%s,%s,%s,%s,%s,%s,%s);
                    """,
                    (order_id, line_no, menuitem_id, qty, price, special, item_customer_id),
                )

                cur.execute(
                    """
                    UPDATE "Order" o
                    SET total_amount = COALESCE(s.total, 0)
                    FROM (
                        SELECT order_id, SUM(quantity * priceatordertime) AS total
                        FROM order_item
                        WHERE order_id=%s
                        GROUP BY order_id
                    ) s
                    WHERE o.order_id=%s;
                    """,
                    (order_id, order_id),
                )

            conn.commit()

        return redirect(url_for("menu", order_id=order_id))

    order_items = q_all(
        """
        SELECT
            oi.line_no,
            mi.item_name,
            oi.quantity,
            oi.priceatordertime,
            (oi.quantity * oi.priceatordertime) AS line_total,
            oi.special_instructions
        FROM order_item oi
        JOIN menu_item mi ON mi.menuitem_id = oi.menuitem_id
        WHERE oi.order_id=%s
        ORDER BY oi.line_no;
        """,
        (order_id,),
    )

    ord_meta = q_one(
        'SELECT order_status, total_amount FROM "Order" WHERE order_id=%s;',
        (order_id,),
    )
    order_status = ord_meta[0]
    order_total = ord_meta[1]

    return render_template(
        "menu.html",
        order_id=order_id,
        items=items,
        order_items=order_items,
        order_status=order_status,
        order_total=order_total,
    )


# ----------------------
# Checkout (Pay + Delivery)
# ----------------------

@app.route("/checkout/<int:order_id>", methods=["GET", "POST"])
def checkout(order_id):
    if request.method == "POST":
        method = request.form.get("payment_method")

        tx_id = next_id("paymenttransaction", "transaction_id")

        with get_conn() as conn:
            with conn.cursor() as cur:
                payer_fallback = session.get(f"order_organizer_{order_id}")

                cur.execute('UPDATE "Order" SET order_status=%s WHERE order_id=%s;', ("confirmed", order_id))

                cur.execute(
                    """
                    INSERT INTO paymenttransaction
                    (transaction_id, order_id, payer_customer_id, amount, currency,
                     transaction_time, payment_method, payment_status)
                    SELECT %s, o.order_id,
                           CASE WHEN o.customer_id IS NULL THEN %s ELSE o.customer_id END,
                           o.total_amount, 'EUR',
                           CURRENT_TIMESTAMP, %s, 'successful'
                    FROM "Order" o
                    WHERE o.order_id=%s;
                    """,
                    (tx_id, payer_fallback, method, order_id),
                )

                # Create delivery
                delivery_id = next_id("delivery", "delivery_id")
                cur.execute(
                    """
                    INSERT INTO delivery (delivery_id, order_id, delivery_status, delivery_fee)
                    VALUES (%s, %s, 'assigned', 3.50);
                    """,
                    (delivery_id, order_id),
                )

                # Assign courier
                courier = q_one("SELECT courier_id FROM courier ORDER BY RANDOM() LIMIT 1;")
                if courier:
                    cur.execute(
                        """
                        INSERT INTO assigned (delivery_id, courier_id, assignedat)
                        VALUES (%s, %s, CURRENT_TIMESTAMP);
                        """,
                        (delivery_id, courier[0]),
                    )

                # Mark delivered (fits your trigger logic)
                cur.execute(
                    """
                    UPDATE delivery
                    SET delivery_status='delivered', dropoff_time=CURRENT_TIMESTAMP
                    WHERE delivery_id=%s;
                    """,
                    (delivery_id,),
                )

                cur.execute('UPDATE "Order" SET order_status=%s WHERE order_id=%s;', ("delivered", order_id))

            conn.commit()

        return redirect(url_for("success", order_id=order_id))

    ord_meta = q_one('SELECT total_amount FROM "Order" WHERE order_id=%s;', (order_id,))
    order_total = ord_meta[0] if ord_meta else 0
    return render_template("checkout.html", order_id=order_id, order_total=order_total)


# ----------------------
# Success
# ----------------------

@app.get("/success/<int:order_id>")
def success(order_id):
    return render_template("success.html", order_id=order_id)


if __name__ == "__main__":
    app.run(debug=True)