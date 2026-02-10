"""Quick test script for Batch 2: Admin Dashboard + Muse Profiles"""

import os
import sys

# Setup
os.chdir(os.path.dirname(os.path.abspath(__file__)))

from app import app, get_db

app.config['TESTING'] = True
app.config['WTF_CSRF_ENABLED'] = False  # Disable CSRF for testing

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
        # Print a snippet of response for debugging
        snippet = response.data[:500].decode(errors='replace')
        print(f"         Response snippet: {snippet[:200]}...")
    return passed

results = []

print("\n=== BATCH 2 TESTS: Admin Dashboard + Muse Profiles ===\n")

# --- 1. Login as admin ---
print("1. Admin Login")
r = client.post('/auth/login', data={
    'email': 'admin@pantiesfan.com',
    'password': 'admin123'
}, follow_redirects=True)
results.append(test("Admin login", r, 200, "Admin"))

# --- 2. Admin Dashboard ---
print("\n2. Admin Dashboard")
r = client.get('/admin')
results.append(test("Dashboard loads", r, 200, "Admin Dashboard"))
results.append(test("Stats visible", r, 200, "Total Auctions"))
results.append(test("Auctions table visible", r, 200, "All Auctions"))
results.append(test("Seed auction visible", r, 200, "Silk Lace Nightset"))

# --- 3. Admin Auction Create Form ---
print("\n3. New Auction Form")
r = client.get('/admin/auction/new')
results.append(test("New auction form loads", r, 200, "Create New Auction"))
results.append(test("Muse dropdown present", r, 200, "Mistress_V"))

# --- 4. Create New Auction ---
print("\n4. Create Auction (POST)")
# Create a fake image file
from io import BytesIO
fake_image = BytesIO(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100)
fake_image.name = 'test.png'

r = client.post('/admin/auction/new', data={
    'title': 'Test Auction from Script',
    'description': 'A test description',
    'muse_id': 1,
    'category': 'panties',
    'wear_duration': '1 day',
    'starting_bid': 100.0,
    'duration_hours': 24,
    'status': 'live',
    'image': (BytesIO(b'\x89PNG\r\n\x1a\n' + b'\x00' * 100), 'test.png')
}, content_type='multipart/form-data', follow_redirects=True)
results.append(test("Auction created (redirect to dashboard)", r, 200, "Test Auction from Script"))

# Verify it's in DB
conn = get_db()
new_auction = conn.execute("SELECT * FROM auctions WHERE title = 'Test Auction from Script'").fetchone()
conn.close()
results.append(test("Auction in database", type('R', (), {'status_code': 200 if new_auction else 500, 'data': b''})(), 200))
if new_auction:
    img = new_auction['image']
    img_ok = img.startswith('uploads/')
    results.append(test(f"Image path has uploads/ prefix ({img})", type('R', (), {'status_code': 200 if img_ok else 500, 'data': b''})(), 200))

# --- 5. Edit Auction ---
print("\n5. Edit Auction")
r = client.get('/admin/auction/1/edit')
results.append(test("Edit form loads for auction 1", r, 200, "Edit Auction"))
results.append(test("Pre-filled title", r, 200, "Silk Lace Nightset"))

r = client.post('/admin/auction/1/edit', data={
    'title': 'Silk Lace Nightset (UPDATED)',
    'description': 'Updated description',
    'muse_id': 1,
    'category': 'set',
    'wear_duration': '2 days',
    'status': 'live',
}, content_type='multipart/form-data', follow_redirects=True)
results.append(test("Auction updated", r, 200, "Silk Lace Nightset (UPDATED)"))

# --- 6. View Auction Bids ---
print("\n6. Auction Bids View")
r = client.get('/admin/auction/1/bids')
results.append(test("Bids page loads", r, 200, "Bid History"))

# --- 7. Extend Auction ---
print("\n7. Extend Auction")
r = client.post('/admin/auction/1/extend', data={'minutes': 30}, follow_redirects=True)
results.append(test("Auction extended", r, 200, "extended by 30 minutes"))

# --- 8. End Auction ---
print("\n8. End Auction")
r = client.post('/admin/auction/4/end', follow_redirects=True)
results.append(test("Auction ended", r, 200, "Auction ended"))

# --- 9. Muse Management ---
print("\n9. Muse Management")
r = client.get('/admin/muses')
results.append(test("Muses page loads", r, 200, "Muse Management"))
results.append(test("Seed muses visible", r, 200, "FitGirl_99"))

# --- 10. New Muse Form ---
print("\n10. New Muse Form")
r = client.get('/admin/muse/new')
results.append(test("New muse form loads", r, 200, "Add New Muse"))

# --- 11. Create Muse ---
print("\n11. Create Muse (POST)")
r = client.post('/admin/muse/new', data={
    'display_name': 'TestMuse_Script',
    'bio': 'A muse created by the test script.',
}, content_type='multipart/form-data', follow_redirects=True)
results.append(test("Muse created", r, 200, "TestMuse_Script"))

# --- 12. Edit Muse ---
print("\n12. Edit Muse")
r = client.get('/admin/muse/1/edit')
results.append(test("Edit muse form loads", r, 200, "Edit Muse"))
results.append(test("Pre-filled muse name", r, 200, "Mistress_V"))

r = client.post('/admin/muse/1/edit', data={
    'display_name': 'Mistress_V_Updated',
    'bio': 'Updated bio for testing',
    'verification': 'verified',
}, content_type='multipart/form-data', follow_redirects=True)
results.append(test("Muse updated", r, 200, "Mistress_V_Updated"))

# --- 13. Public Muse Profile ---
print("\n13. Public Muse Profile")
r = client.get('/muse/1')
results.append(test("Muse profile loads", r, 200, "Mistress_V_Updated"))
results.append(test("Profile shows verified badge", r, 200, "Verified Muse"))
results.append(test("Profile shows auctions", r, 200, "Silk Lace Nightset"))

# --- 14. Non-admin access blocked ---
print("\n14. Access Control")
client.get('/auth/logout')  # Logout admin

# Register normal user
client.post('/auth/register', data={
    'email': 'buyer@test.com',
    'password': 'testtest123',
    'password_confirm': 'testtest123',
    'display_name': 'TestBuyer',
    'dob': '1990-01-01',
    'age_confirm': 'on',
    'terms_confirm': 'on',
}, follow_redirects=True)

r = client.get('/admin')
results.append(test("Non-admin gets 403 on dashboard", r, 403))

r = client.get('/admin/muses')
results.append(test("Non-admin gets 403 on muses", r, 403))

r = client.get('/admin/auction/new')
results.append(test("Non-admin gets 403 on new auction", r, 403))

# --- 15. Muse profile accessible without login ---
client.get('/auth/logout')
r = client.get('/muse/1')
results.append(test("Muse profile accessible without login", r, 200, "Mistress_V_Updated"))

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
