"""Test script for Batch 3: Payment Flow + Order Tracking + Buyer Dashboard"""

import os
import sys
from datetime import datetime, timedelta, timezone

os.chdir(os.path.dirname(os.path.abspath(__file__)))

# Remove existing DB for fresh start
if os.path.exists('panties_fan.db'):
    os.remove('panties_fan.db')

from app import app, get_db, create_payment_for_winner, end_expired_auctions

app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False

client = app.test_client()

def test(name, response, expected_code=200, check_text=None):
    passed = response.status_code == expected_code
    if check_text and passed:
        passed = check_text.encode() in response.data
    status = "PASS" if passed else "FAIL"
    detail = f"got {response.status_code}"
    if check_text and not (check_text.encode() in response.data):
        detail += f", text '{check_text}' NOT found"
    print(f"  [{status}] {name} ({detail})")
    if not passed:
        snippet = response.data[:300].decode(errors='replace')
        print(f"         Response snippet: {snippet[:200]}...")
    return passed

def test_bool(name, condition):
    status = "PASS" if condition else "FAIL"
    print(f"  [{status}] {name}")
    return condition

results = []

print("\n=== BATCH 3 TESTS: Payment Flow + Order Tracking + Buyer Dashboard ===\n")

# --- 1. Setup: Register a buyer + login admin ---
print("1. Setup: Create buyer and login admin")

# Register buyer
r = client.post('/auth/register', data={
    'email': 'buyer@test.com',
    'password': 'buyerpass123',
    'password_confirm': 'buyerpass123',
    'display_name': 'TestBuyer',
    'dob': '1990-01-01',
    'age_confirm': 'on',
    'terms_confirm': 'on',
}, follow_redirects=True)
results.append(test("Buyer registered", r, 200, "TestBuyer"))

# Logout buyer
client.get('/auth/logout')

# Login admin
r = client.post('/auth/login', data={
    'email': 'admin@pantiesfan.com',
    'password': 'admin123'
}, follow_redirects=True)
results.append(test("Admin logged in", r, 200, "Admin"))

# --- 2. Simulate auction end with winner ---
print("\n2. Simulate auction end with winning bid")

# First, place a bid as buyer (need to switch users)
client.get('/auth/logout')
client.post('/auth/login', data={'email': 'buyer@test.com', 'password': 'buyerpass123'})

# Place bid on auction 1 via API
import json
r = client.post('/api/bid/1', data=json.dumps({'amount': 200.00}),
                content_type='application/json')
data = json.loads(r.data)
results.append(test_bool("Bid placed successfully", data.get('success') == True))

# Now manually end the auction and trigger payment creation
conn = get_db()
now_str = datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
# Set auction 1 to ended by making its ends_at in the past
past = (datetime.now(timezone.utc) - timedelta(minutes=1)).strftime('%Y-%m-%dT%H:%M:%SZ')
conn.execute("UPDATE auctions SET ends_at = ? WHERE id = 1", (past,))
conn.commit()

# Trigger end_expired_auctions
end_expired_auctions(conn)

# Verify payment record was created
payment = conn.execute('SELECT * FROM payments WHERE auction_id = 1').fetchone()
results.append(test_bool("Payment record created for auction 1", payment is not None))
results.append(test_bool("Payment status is 'awaiting_payment'", payment and payment['status'] == 'awaiting_payment'))
results.append(test_bool("Payment amount matches bid ($200.00)", payment and payment['amount'] == 200.00))
results.append(test_bool("Payment token exists", payment and payment['payment_token'] is not None))

# Verify notification was created
notif = conn.execute("SELECT * FROM notifications WHERE user_id = 2 AND type = 'auction_won'").fetchone()
results.append(test_bool("Winner notification created", notif is not None))
results.append(test_bool("Notification has payment link", notif and '/pay/' in (notif['link'] or '')))

payment_token = payment['payment_token'] if payment else 'NONE'
conn.close()

# --- 3. Buyer Dashboard ---
print("\n3. Buyer Dashboard")

r = client.get('/dashboard')
results.append(test("Dashboard loads", r, 200, "My Dashboard"))
results.append(test("Shows won auction", r, 200, "Silk Lace Nightset"))
results.append(test("Shows notification", r, 200, "You won an auction"))
results.append(test("Shows pay now link", r, 200, "Pay Now"))

# --- 4. Payment Page ---
print("\n4. Payment Page")

r = client.get(f'/pay/{payment_token}')
results.append(test("Payment page loads", r, 200, "Complete Your Payment"))
results.append(test("Shows item title", r, 200, "Silk Lace Nightset"))
results.append(test("Shows winning bid amount", r, 200, "200.00"))
results.append(test("Shows address form", r, 200, "Shipping Address"))
# Note: Payment methods only shown after address is saved — tested in step 5b

# --- 5. Save shipping address ---
print("\n5. Shipping Address")

r = client.post(f'/pay/{payment_token}/address', data={
    'full_name': 'John Doe',
    'address_line1': '123 Main St',
    'address_line2': 'Apt 4B',
    'city': 'New York',
    'state': 'NY',
    'postal_code': '10001',
    'country': 'US',
    'phone': '+1-555-1234',
}, follow_redirects=True)
results.append(test("Address saved", r, 200, "Shipping address saved"))

# Verify address in DB
conn = get_db()
addr = conn.execute('SELECT * FROM shipping_addresses WHERE user_id = 2').fetchone()
results.append(test_bool("Address saved in DB", addr is not None))
results.append(test_bool("Address country is US", addr and addr['country'] == 'US'))
conn.close()

# Reload payment page - should now show shipping cost AND payment methods
r = client.get(f'/pay/{payment_token}')
results.append(test("Shows US shipping estimate", r, 200, "85.00"))
results.append(test("Shows payment methods now", r, 200, "Credit Card"))
results.append(test("Shows crypto option", r, 200, "Cryptocurrency"))

# --- 6. Confirm payment method ---
print("\n6. Confirm Payment Method")

r = client.post(f'/pay/{payment_token}/confirm', data={
    'method': 'card'
}, follow_redirects=True)
results.append(test("Payment method confirmed", r, 200, "Payment via card initiated"))

# Verify payment status changed
conn = get_db()
payment_now = conn.execute('SELECT * FROM payments WHERE payment_token = ?', (payment_token,)).fetchone()
results.append(test_bool("Payment status now 'pending'", payment_now and payment_now['status'] == 'pending'))
results.append(test_bool("Payment processor is 'card'", payment_now and payment_now['processor'] == 'card'))

# Verify shipment record created
shipment = conn.execute('SELECT * FROM shipments WHERE payment_id = ?', (payment_now['id'],)).fetchone()
results.append(test_bool("Shipment record created", shipment is not None))
results.append(test_bool("Shipping cost is $85.00 (US rate)", shipment and shipment['shipping_cost'] == 85.00))
conn.close()

# Payment page should show "Payment Processing" status
r = client.get(f'/pay/{payment_token}')
results.append(test("Shows processing status", r, 200, "Payment Processing"))

# --- 7. Admin: Order Management ---
print("\n7. Admin: Order Management")

client.get('/auth/logout')
client.post('/auth/login', data={'email': 'admin@pantiesfan.com', 'password': 'admin123'})

r = client.get('/admin/orders')
results.append(test("Admin orders page loads", r, 200, "Order Management"))
results.append(test("Shows order for auction 1", r, 200, "Silk Lace Nightset"))
results.append(test("Shows buyer name", r, 200, "TestBuyer"))
results.append(test("Shows pending status", r, 200, "PENDING"))

# --- 8. Admin: Mark as Paid ---
print("\n8. Admin: Mark Paid")

conn = get_db()
pid = conn.execute('SELECT id FROM payments WHERE auction_id = 1').fetchone()['id']
conn.close()

r = client.post(f'/admin/order/{pid}/mark-paid', data={
    'processor_txn': 'CCBILL-TEST-12345'
}, follow_redirects=True)
results.append(test("Marked as paid", r, 200, "Payment marked as paid"))

conn = get_db()
payment_after = conn.execute('SELECT * FROM payments WHERE id = ?', (pid,)).fetchone()
results.append(test_bool("Payment status now 'paid'", payment_after['status'] == 'paid'))
results.append(test_bool("Processor txn recorded", payment_after['processor_txn'] == 'CCBILL-TEST-12345'))

# Verify buyer notification was created
notif_paid = conn.execute("SELECT * FROM notifications WHERE user_id = 2 AND type = 'payment_confirmed'").fetchone()
results.append(test_bool("Buyer notified of payment confirmation", notif_paid is not None))
conn.close()

# --- 9. Admin: Ship Order ---
print("\n9. Admin: Ship Order")

r = client.post(f'/admin/order/{pid}/ship', data={
    'tracking_number': 'DHL-BKK-12345678',
    'carrier': 'DHL'
}, follow_redirects=True)
results.append(test("Order shipped", r, 200, "DHL-BKK-12345678"))

conn = get_db()
shipment_after = conn.execute('SELECT * FROM shipments WHERE payment_id = ?', (pid,)).fetchone()
results.append(test_bool("Shipment status is 'shipped'", shipment_after['status'] == 'shipped'))
results.append(test_bool("Tracking number saved", shipment_after['tracking_number'] == 'DHL-BKK-12345678'))

# Verify buyer notification
notif_ship = conn.execute("SELECT * FROM notifications WHERE user_id = 2 AND type = 'order_shipped'").fetchone()
results.append(test_bool("Buyer notified of shipment", notif_ship is not None))
conn.close()

# --- 10. Buyer sees shipping status ---
print("\n10. Buyer Sees Shipping Status")

client.get('/auth/logout')
client.post('/auth/login', data={'email': 'buyer@test.com', 'password': 'buyerpass123'})

r = client.get(f'/pay/{payment_token}')
results.append(test("Buyer sees shipped status", r, 200, "Your Order Has Shipped"))
results.append(test("Buyer sees tracking number", r, 200, "DHL-BKK-12345678"))

r = client.get('/dashboard')
results.append(test("Dashboard shows shipped status", r, 200, "Shipped"))

# --- 11. Admin: Mark Delivered ---
print("\n11. Admin: Mark Delivered")

client.get('/auth/logout')
client.post('/auth/login', data={'email': 'admin@pantiesfan.com', 'password': 'admin123'})

r = client.post(f'/admin/order/{pid}/deliver', follow_redirects=True)
results.append(test("Order marked delivered", r, 200, "delivered"))

conn = get_db()
payment_final = conn.execute('SELECT * FROM payments WHERE id = ?', (pid,)).fetchone()
results.append(test_bool("Payment status is 'completed'", payment_final['status'] == 'completed'))

shipment_final = conn.execute('SELECT * FROM shipments WHERE payment_id = ?', (pid,)).fetchone()
results.append(test_bool("Shipment status is 'delivered'", shipment_final['status'] == 'delivered'))
results.append(test_bool("Delivered_at timestamp set", shipment_final['delivered_at'] is not None))

# Check muse sales count updated
muse = conn.execute('SELECT total_sales FROM muse_profiles WHERE id = 1').fetchone()
results.append(test_bool("Muse total_sales incremented", muse['total_sales'] == 1))

notif_deliver = conn.execute("SELECT * FROM notifications WHERE user_id = 2 AND type = 'order_delivered'").fetchone()
results.append(test_bool("Buyer notified of delivery", notif_deliver is not None))
conn.close()

# --- 12. Buyer sees completed order ---
print("\n12. Buyer Final Status")

client.get('/auth/logout')
client.post('/auth/login', data={'email': 'buyer@test.com', 'password': 'buyerpass123'})

r = client.get(f'/pay/{payment_token}')
results.append(test("Payment page shows completed", r, 200, "Order Complete"))

r = client.get('/dashboard')
results.append(test("Dashboard shows delivered", r, 200, "Delivered"))

# --- 13. Notification count API ---
print("\n13. Notifications API")

# Mark all as read first by visiting dashboard, then create a new one
conn = get_db()
conn.execute("INSERT INTO notifications (user_id, type, title, message, created_at) VALUES (2, 'test', 'Test', 'Test msg', datetime('now'))")
conn.commit()
conn.close()

r = client.get('/api/notifications/count')
data = json.loads(r.data)
results.append(test_bool("Notification count API returns count", data.get('count', 0) >= 1))

# --- 14. Access Control ---
print("\n14. Access Control")

# Buyer can't see other's payment page
conn = get_db()
# Create a dummy payment for another user
conn.execute('''
    INSERT INTO payments (auction_id, buyer_id, amount, status, payment_token, created_at)
    VALUES (2, 1, 100, 'pending', 'other-token-123', datetime('now'))
''')
conn.commit()
conn.close()

r = client.get('/pay/other-token-123')
results.append(test("Buyer can't see other's payment", r, 403))

# Non-admin can't access admin orders
r = client.get('/admin/orders')
results.append(test("Buyer can't access admin orders", r, 403))

# Non-logged-in user can't access dashboard
client.get('/auth/logout')
r = client.get('/dashboard', follow_redirects=True)
results.append(test("Unauthenticated user redirected from dashboard", r, 200, "Sign In"))

# --- 15. Address validation ---
print("\n15. Address Validation")

client.post('/auth/login', data={'email': 'buyer@test.com', 'password': 'buyerpass123'})

r = client.post('/dashboard/address', data={
    'full_name': '',
    'address_line1': '',
    'city': '',
    'postal_code': '',
    'country': '',
}, follow_redirects=True)
results.append(test("Empty address rejected", r, 200, "required address fields"))

# --- Summary ---
passed = sum(1 for r in results if r)
total = len(results)
print(f"\n{'='*50}")
print(f"Results: {passed}/{total} tests passed")
if passed == total:
    print("ALL TESTS PASSED!")
else:
    print(f"FAILED: {total - passed} tests")
print(f"{'='*50}\n")
