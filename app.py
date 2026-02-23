from db import get_conn
from decimal import Decimal

import time

# ----------------------------
# Pretty-print helpers for TX demos
# ----------------------------

def money(x):
    if x is None:
        return "0.00"
    try:
        return f"{Decimal(x):.2f}"
    except Exception:
        return str(x)


def print_kv(title: str, pairs):
    print(f"\n{title}")
    for k, v in pairs:
        print(f"  - {k}: {v}")


def fetch_order_header(order_id: int):
    rows = run_select(
        """
        SELECT order_id, restaurant_id, customer_id, group_id, order_status, order_timestamp, delivery_address, total_amount
        FROM "Order"
        WHERE order_id=%s;
        """,
        (order_id,),
    )
    return rows[0] if rows else None


def fetch_order_items(order_id: int):
    return run_select(
        """
        SELECT oi.line_no, oi.menuitem_id, mi.item_name, oi.quantity, oi.priceatordertime,
               (oi.quantity * oi.priceatordertime) AS line_total,
               oi.special_instructions
        FROM order_item oi
        JOIN menu_item mi ON mi.menuitem_id = oi.menuitem_id
        WHERE oi.order_id=%s
        ORDER BY oi.line_no;
        """,
        (order_id,),
    )


def print_order_snapshot(order_id: int, label: str):
    hdr = fetch_order_header(order_id)
    if not hdr:
        print(f"\n{label}: (order {order_id} not found)")
        return
    (oid, rid, cid, gid, status, ts, addr, total) = hdr
    who = f"customer_id={cid}" if cid is not None else f"group_id={gid}"
    print_kv(f"{label}", [
        ("order_id", oid),
        ("restaurant_id", rid),
        ("who", who),
        ("status", status),
        ("timestamp", ts),
        ("delivery_address", addr),
        ("total_amount (triggered)", f"€{money(total)}"),
    ])
    items = fetch_order_items(order_id)
    if items:
        print("  Items:")
        for ln, mid, name, qty, price, line_total, notes in items:
            notes_txt = notes if notes else "-"
            print(f"    * line {ln}: {name} (id {mid}) | qty {qty} | €{money(price)} | line €{money(line_total)} | notes: {notes_txt}")
    else:
        print("  Items: (none)")


def print_payment_snapshot(order_id: int):
    rows = run_select(
        """
        SELECT transaction_id, payment_method, payment_status, amount, transaction_time
        FROM paymenttransaction
        WHERE order_id=%s
        ORDER BY transaction_time;
        """,
        (order_id,),
    )
    if not rows:
        print("\nPayments: (none)")
        return
    print("\nPayments:")
    for tid, method, status, amount, ttime in rows:
        print(f"  - tx {tid}: {method} | {status} | €{money(amount)} | {ttime}")


def explain_tx1_header(order_id: int, transaction_id: int):
    print("\n✅ Transaction 1 completed.")
    print("What this transaction demonstrates (Assignment 6):")
    print("  1) INSERT Order (created)")
    print("  2) INSERT 2 Order_Items → trigger recalculates total_amount")
    print("  3) UPDATE quantity → trigger recalculates")
    print("  4) DELETE one item → trigger recalculates")
    print("  5) UPDATE Order status to confirmed")
    print("  6) INSERT successful Payment = total_amount → payment trigger checks it does not exceed")
    print(f"\nCreated order_id={order_id}, payment transaction_id={transaction_id}.")

# ----------------------------
# DB helpers
# ----------------------------

def run_select(sql: str, params=None):
    with get_conn() as conn:
        with conn.cursor() as cur:
            cur.execute(sql, params or ())
            return cur.fetchall()


def run_in_transaction(steps):
    """steps = list of tuples (sql, params, fetchone|fetchall|None)
    Returns dict of named results (if you pass a string key instead of fetchmode).
    """
    results = {}
    with get_conn() as conn:
        try:
            with conn.cursor() as cur:
                for sql, params, fetch in steps:
                    cur.execute(sql, params or ())
                    if fetch == "one":
                        results[sql] = cur.fetchone()
                    elif fetch == "all":
                        results[sql] = cur.fetchall()
                    elif isinstance(fetch, str):
                        # store under custom key
                        if fetch.startswith("one:"):
                            key = fetch.split(":", 1)[1]
                            results[key] = cur.fetchone()
                        elif fetch.startswith("all:"):
                            key = fetch.split(":", 1)[1]
                            results[key] = cur.fetchall()
            conn.commit()
            return True, results
        except Exception as e:
            conn.rollback()
            return False, str(e)


def next_int_id(table: str, column: str) -> int:
    # table must be a safe identifier already (do not use user input here)
    rows = run_select(f"SELECT COALESCE(MAX({column}), 0) + 1 FROM {table};")
    return int(rows[0][0])


# ----------------------------
# Customer-facing UI Flow
# ----------------------------

def find_customer_by_name(first_name: str, last_name: str):
    rows = run_select(
        """SELECT customer_id, email_address
           FROM participantcustomer
           WHERE first_name = %s AND last_name = %s
           ORDER BY customer_id
           LIMIT 1;""",
        (first_name, last_name),
    )
    return rows[0] if rows else None


def choose_restaurant():
    rows = run_select(
        "SELECT restaurant_id, name, cuisine_type FROM restaurant ORDER BY restaurant_id;"
    )
    print("\n🍴 Choose a restaurant:")
    for rid, name, cuisine in rows:
        print(f"  {rid:>2} | {name} | {cuisine}")

    while True:
        raw = input("Restaurant number: ").strip()
        if raw.isdigit():
            rid = int(raw)
            if any(r[0] == rid for r in rows):
                return rid
        print("Please type a valid restaurant number.")


def list_menu_items_for_restaurant(restaurant_id: int):
    # Proper separation: Restaurant -> Menu -> Contains -> Menu_Item
    rows = run_select(
        """
        SELECT mi.menuitem_id, mi.item_name, mi.category, mi.price, mi.isavailable
        FROM menu m
        JOIN contains_menuitem c ON c.menu_id = m.menu_id
        JOIN menu_item mi ON mi.menuitem_id = c.menuitem_id
        WHERE m.restaurant_id = %s
        ORDER BY mi.menuitem_id;
        """,
        (restaurant_id,),
    )
    return rows


def start_new_order_flow():
    print("\n========== EatALot ==========")
    print("Welcome! Let's place your order.\n")

    first_name = input("First name: ").strip()
    last_name = input("Last name: ").strip()

    cust = find_customer_by_name(first_name, last_name)
    if not cust:
        print("\n❌ Customer not found in the database.")
        print("(For this class demo, customers come from the insertion script.)")
        return

    customer_id, email = cust

    restaurant_id = choose_restaurant()

    delivery_address = input("Delivery address: ").strip()
    if not delivery_address:
        print("Delivery address is required.")
        return

    order_id = next_int_id('"Order"', 'order_id')

    ok, res = run_in_transaction([
        (
            """
            INSERT INTO "Order"
              (order_id, restaurant_id, order_timestamp, order_status, delivery_address, total_amount, customer_id, group_id)
            VALUES
              (%s, %s, CURRENT_TIMESTAMP, 'created', %s, 0, %s, NULL);
            """,
            (order_id, restaurant_id, delivery_address, customer_id),
            None,
        )
    ])
    if not ok:
        print("\n❌ Could not create order.")
        print(res)
        return

    print(f"\n✅ Order created! (Order #{order_id})")

    # Items
    print("\n🛒 Now choose items. Type 'done' when finished.")
    line_no = 1

    while True:
        items = list_menu_items_for_restaurant(restaurant_id)
        if not items:
            print("\n⚠️ No menu items found for this restaurant.")
            print("(Check Menu + Contains_MenuItem data.)")
            return

        print("\nMenu:")
        for mid, name, cat, price, avail in items:
            mark = "✅" if avail else "❌"
            print(f"  {mid:>2} | {name} | {cat} | €{price:.2f} | {mark}")

        choice = input("Item number (or done): ").strip().lower()
        if choice == "done":
            break
        if not choice.isdigit():
            print("Please type an item number or 'done'.")
            continue

        menuitem_id = int(choice)
        valid_ids = {i[0] for i in items}
        if menuitem_id not in valid_ids:
            print("That item is not in this restaurant's menu.")
            continue

        qty_raw = input("Quantity: ").strip()
        if not qty_raw.isdigit() or int(qty_raw) <= 0:
            print("Quantity must be a positive whole number.")
            continue
        qty = int(qty_raw)

        notes = input("Special instructions (optional, press Enter to skip): ").strip()
        if notes == "" or notes == "0":
            notes = None

        # price-at-order-time from menu_item
        price = run_select(
            "SELECT price FROM menu_item WHERE menuitem_id=%s;",
            (menuitem_id,),
        )[0][0]

        ok, res = run_in_transaction([
            (
                """
                INSERT INTO order_item
                  (order_id, line_no, menuitem_id, quantity, priceatordertime, special_instructions, customer_id)
                VALUES
                  (%s,%s,%s,%s,%s,%s,%s);
                """,
                (order_id, line_no, menuitem_id, qty, price, notes, customer_id),
                None,
            )
        ])
        if not ok:
            print("\n❌ Could not add item.")
            print(res)
            return

        print("✅ Added! (Order total recalculated by trigger)")
        line_no += 1

    # Confirm
    ok, res = run_in_transaction([
        ("UPDATE \"Order\" SET order_status='confirmed' WHERE order_id=%s;", (order_id,), None)
    ])
    if not ok:
        print("\n❌ Could not confirm order.")
        print(res)
        return

    # Payment
    print("\n💳 Payment")
    method = input("Choose payment method (card/paypal): ").strip().lower()
    if method not in ("card", "paypal"):
        method = "card"

    transaction_id = next_int_id("paymenttransaction", "transaction_id")

    print("Processing payment...")
    time.sleep(0.4)

    ok, res = run_in_transaction([
        (
            """
            INSERT INTO paymenttransaction
              (transaction_id, order_id, payer_customer_id, amount, currency, transaction_time, payment_method, payment_status)
            SELECT
              %s, o.order_id, o.customer_id, o.total_amount, 'EUR', CURRENT_TIMESTAMP, %s, 'successful'
            FROM "Order" o
            WHERE o.order_id = %s;
            """,
            (transaction_id, method.capitalize(), order_id),
            None,
        )
    ])

    if not ok:
        print("\n❌ Payment failed.")
        print(res)
        return

    print("✅ Payment successful!")

    # Courier + Delivery lifecycle (auto)
    print("\n🚴 Assigning a courier...")
    courier = run_select("SELECT courier_id, first_name, last_name FROM courier ORDER BY RANDOM() LIMIT 1;")
    courier_id, cfn, cln = courier[0]
    print(f"Courier assigned: {cfn} {cln}")

    delivery_id = next_int_id("delivery", "delivery_id")

    ok, res = run_in_transaction([
        (
            "INSERT INTO delivery (delivery_id, order_id, delivery_status, pickup_time, dropoff_time, delivery_fee) VALUES (%s,%s,'assigned',NULL,NULL,3.50);",
            (delivery_id, order_id),
            None,
        ),
        (
            "INSERT INTO assigned (delivery_id, courier_id, assignedat, acceptedat) VALUES (%s,%s,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);",
            (delivery_id, courier_id),
            None,
        ),
        (
            "UPDATE delivery SET delivery_status='picked_up', pickup_time=CURRENT_TIMESTAMP WHERE delivery_id=%s;",
            (delivery_id,),
            None,
        ),
        (
            "UPDATE delivery SET delivery_status='delivered', dropoff_time=CURRENT_TIMESTAMP WHERE delivery_id=%s;",
            (delivery_id,),
            None,
        ),
        # must be after delivery delivered (trigger 2)
        (
            "UPDATE \"Order\" SET order_status='delivered' WHERE order_id=%s;",
            (order_id,),
            None,
        ),
    ])

    if not ok:
        print("\n❌ Could not complete delivery lifecycle.")
        print(res)
        return

    print("✅ Delivery completed!")
    print("\n🎉 Your order has been delivered. Enjoy your meal!\n")

    # Print receipt-like summary (using your view)
    show_order_payment_summary(order_id)


def show_order_payment_summary(order_id: int):
    # Friendly: show header + items + payments + view result
    print_order_snapshot(order_id, "Current Order Snapshot")
    print_payment_snapshot(order_id)

    rows = run_select(
        "SELECT * FROM v_order_payment_summary WHERE order_id=%s;",
        (order_id,),
    )
    if rows:
        print("\n--- View: v_order_payment_summary ---")
        print(rows[0])
    else:
        print("\n--- View: v_order_payment_summary ---")
        print("(no row returned)")


# ----------------------------
# Assignment 6 Transactions (Demo)
# These are run inside the application.
# They auto-pick new IDs so you can run multiple times.
# ----------------------------

def tx1_individual_lifecycle_demo():
    print("\n--- Transaction 1 (Individual order lifecycle demo) ---")

    # pick a customer and restaurant that exist
    customer_id = run_select("SELECT customer_id FROM participantcustomer ORDER BY customer_id LIMIT 1;")[0][0]
    restaurant_id = run_select("SELECT restaurant_id FROM restaurant ORDER BY restaurant_id LIMIT 1;")[0][0]
    delivery_address = "Demo Address, Hamburg"

    order_id = next_int_id('"Order"', 'order_id')

    # choose 2 items from that restaurant menu
    items = list_menu_items_for_restaurant(restaurant_id)
    if len(items) < 2:
        print("Not enough menu items for the first restaurant to run TX1.")
        return
    item_a = items[0]
    item_b = items[1]

    tx_id = next_int_id("paymenttransaction", "transaction_id")

    ok, res = run_in_transaction([
        (
            "INSERT INTO \"Order\" (order_id, restaurant_id, order_timestamp, order_status, delivery_address, total_amount, customer_id, group_id) VALUES (%s,%s,CURRENT_TIMESTAMP,'created',%s,0,%s,NULL);",
            (order_id, restaurant_id, delivery_address, customer_id),
            None,
        ),
        (
            "INSERT INTO order_item (order_id,line_no,menuitem_id,quantity,priceatordertime,special_instructions,customer_id) VALUES (%s,1,%s,1,%s,%s,%s);",
            (order_id, item_a[0], item_a[3], "no onions", customer_id),
            None,
        ),
        (
            "INSERT INTO order_item (order_id,line_no,menuitem_id,quantity,priceatordertime,special_instructions,customer_id) VALUES (%s,2,%s,1,%s,%s,%s);",
            (order_id, item_b[0], item_b[3], None, customer_id),
            None,
        ),
        # update qty line 1
        (
            "UPDATE order_item SET quantity=2 WHERE order_id=%s AND line_no=1;",
            (order_id,),
            None,
        ),
        # delete line 2
        (
            "DELETE FROM order_item WHERE order_id=%s AND line_no=2;",
            (order_id,),
            None,
        ),
        (
            "UPDATE \"Order\" SET order_status='confirmed' WHERE order_id=%s;",
            (order_id,),
            None,
        ),
        (
            """
            INSERT INTO paymenttransaction
              (transaction_id, order_id, payer_customer_id, amount, currency, transaction_time, payment_method, payment_status)
            SELECT %s, o.order_id, o.customer_id, o.total_amount, 'EUR', CURRENT_TIMESTAMP, 'Card', 'successful'
            FROM "Order" o
            WHERE o.order_id=%s;
            """,
            (tx_id, order_id),
            None,
        ),
    ])

    if not ok:
        print("❌ ROLLBACK (TX1 failed)")
        print(res)
        return

    explain_tx1_header(order_id, tx_id)

    # Show the final state clearly
    print_order_snapshot(order_id, "Final state after TX1")
    print_payment_snapshot(order_id)
    # Also show the view row
    rows = run_select("SELECT * FROM v_order_payment_summary WHERE order_id=%s;", (order_id,))
    print("\n--- View: v_order_payment_summary ---")
    print(rows[0] if rows else "(no row)")


def tx2_revenue_effect_demo():
    print("\n--- Transaction 2 (Restaurant revenue effect demo) ---")

    # pick restaurant_id=1 if exists (common for your view), else first
    r = run_select("SELECT restaurant_id FROM restaurant WHERE restaurant_id=1;")
    restaurant_id = r[0][0] if r else run_select("SELECT restaurant_id FROM restaurant ORDER BY restaurant_id LIMIT 1;")[0][0]

    customer_id = run_select("SELECT customer_id FROM participantcustomer ORDER BY customer_id OFFSET 1 LIMIT 1;")[0][0]
    order_id = next_int_id('"Order"', 'order_id')
    tx_id = next_int_id("paymenttransaction", "transaction_id")

    items = list_menu_items_for_restaurant(restaurant_id)
    if not items:
        print("No menu items found for restaurant; cannot run TX2.")
        return

    # pick first item
    item = items[0]

    ok, res = run_in_transaction([
        (
            "INSERT INTO \"Order\" (order_id, restaurant_id, order_timestamp, order_status, delivery_address, total_amount, customer_id, group_id) VALUES (%s,%s,CURRENT_TIMESTAMP,'created',%s,0,%s,NULL);",
            (order_id, restaurant_id, "Demo Address, Hamburg", customer_id),
            None,
        ),
        (
            "INSERT INTO order_item (order_id,line_no,menuitem_id,quantity,priceatordertime,special_instructions,customer_id) VALUES (%s,1,%s,2,%s,%s,%s);",
            (order_id, item[0], item[3], None, customer_id),
            None,
        ),
        (
            "UPDATE \"Order\" SET order_status='confirmed' WHERE order_id=%s;",
            (order_id,),
            None,
        ),
        (
            """
            INSERT INTO paymenttransaction
              (transaction_id, order_id, payer_customer_id, amount, currency, transaction_time, payment_method, payment_status)
            SELECT %s, o.order_id, o.customer_id, o.total_amount, 'EUR', CURRENT_TIMESTAMP, 'PayPal', 'successful'
            FROM "Order" o
            WHERE o.order_id=%s;
            """,
            (tx_id, order_id),
            None,
        ),
    ])

    if not ok:
        print("❌ ROLLBACK (TX2 failed)")
        print(res)
        return

    print("\n✅ Transaction 2 completed.")
    print("What this transaction demonstrates (Assignment 6):")
    print("  1) INSERT a new Order for a restaurant")
    print("  2) INSERT Order_Items → trigger updates total")
    print("  3) INSERT successful Payment")
    print("  4) SELECT the monthly revenue VIEW to show the effect")
    print(f"Created order_id={order_id}, payment transaction_id={tx_id}.")

    print_order_snapshot(order_id, "Order created in TX2")
    print_payment_snapshot(order_id)


def tx3_delivery_lifecycle_demo():
    print("\n--- Transaction 3 (Delivery lifecycle demo) ---")

    # Choose the most recent confirmed order that does not yet have a delivery
    row = run_select(
        """
        SELECT o.order_id
        FROM "Order" o
        LEFT JOIN delivery d ON d.order_id = o.order_id
        WHERE o.order_status IN ('confirmed','preparing')
          AND d.order_id IS NULL
        ORDER BY o.order_id DESC
        LIMIT 1;
        """
    )
    if not row:
        print("No eligible order found (need a confirmed/preparing order without a delivery).")
        print("Run TX1 or create an order via the UI first.")
        return

    order_id = row[0][0]

    courier = run_select("SELECT courier_id FROM courier ORDER BY RANDOM() LIMIT 1;")[0][0]
    delivery_id = next_int_id("delivery", "delivery_id")

    ok, res = run_in_transaction([
        (
            "INSERT INTO delivery (delivery_id, order_id, delivery_status, pickup_time, dropoff_time, delivery_fee) VALUES (%s,%s,'assigned',NULL,NULL,3.50);",
            (delivery_id, order_id),
            None,
        ),
        (
            "INSERT INTO assigned (delivery_id, courier_id, assignedat, acceptedat) VALUES (%s,%s,CURRENT_TIMESTAMP,CURRENT_TIMESTAMP);",
            (delivery_id, courier),
            None,
        ),
        (
            "UPDATE delivery SET delivery_status='picked_up', pickup_time=CURRENT_TIMESTAMP WHERE delivery_id=%s;",
            (delivery_id,),
            None,
        ),
        (
            "UPDATE delivery SET delivery_status='delivered', dropoff_time=CURRENT_TIMESTAMP WHERE delivery_id=%s;",
            (delivery_id,),
            None,
        ),
        (
            "UPDATE \"Order\" SET order_status='delivered' WHERE order_id=%s;",
            (order_id,),
            None,
        ),
    ])

    if not ok:
        print("❌ ROLLBACK (TX3 failed)")
        print(res)
        return

    print("\n✅ Transaction 3 completed.")
    print("What this transaction demonstrates (Assignment 6):")
    print("  1) INSERT Delivery for an existing confirmed order")
    print("  2) INSERT Assigned (delivery↔courier) with timestamps")
    print("  3) UPDATE Delivery status to picked_up then delivered")
    print("  4) UPDATE Order status to delivered (allowed only because delivered Delivery exists) → trigger enforces")
    print(f"Created delivery_id={delivery_id} for order_id={order_id}.")

    print_order_snapshot(order_id, "Order after TX3")


# ----------------------------
# Main Menu
# ----------------------------

def main():
    while True:
        print("\n========== EatALot DB Application ==========")
        print("[1] Start New Order (customer UI)")
        print("[2] Show Order Payment Summary (view)")
        print("---- Assignment 6 Transactions (demo in-app) ----")
        print("[11] Run Transaction 1 (lifecycle)")
        print("[12] Run Transaction 2 (revenue effect)")
        print("[13] Run Transaction 3 (delivery lifecycle)")
        print("[0] Exit")

        choice = input("Choose: ").strip()

        if choice == "1":
            start_new_order_flow()
        elif choice == "2":
            raw = input("Order ID (for summary): ").strip()
            if raw.isdigit():
                show_order_payment_summary(int(raw))
            else:
                print("Please enter a numeric Order ID.")
        elif choice == "11":
            tx1_individual_lifecycle_demo()
        elif choice == "12":
            tx2_revenue_effect_demo()
        elif choice == "13":
            tx3_delivery_lifecycle_demo()
        elif choice == "0":
            print("Goodbye!")
            break
        else:
            print("Invalid choice.")


if __name__ == "__main__":
    main()