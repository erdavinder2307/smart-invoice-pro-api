import requests
import json
import uuid

BASE_URL = "http://localhost:5000/api"
TEST_USER_ID = f"test_user_{str(uuid.uuid4())}"
HEADERS = {
    "X-User-Id": TEST_USER_ID,
    "Content-Type": "application/json"
}

def test_get_profile_new_user():
    print(f"\n--- Testing GET /profile/me for new user {TEST_USER_ID} ---")
    response = requests.get(f"{BASE_URL}/profile/me", headers=HEADERS)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
    if response.status_code == 200:
        data = response.json()
        if data.get('user_id') == TEST_USER_ID:
            print("✅ SUCCESS: Retrieved default profile for new user")
        else:
            print("❌ FAILURE: User ID mismatch")
    else:
        print("❌ FAILURE: Unexpected status code")

def test_update_profile():
    print(f"\n--- Testing POST /profile/update for user {TEST_USER_ID} ---")
    payload = {
        "name": "Test User",
        "email": "test@example.com",
        "phone": "9876543210",
        "business_name": "Test Corp",
        "gstin": "22AAAAA0000A1Z5",
        "address": "Test Address",
        "default_currency": "USD"
    }
    
    response = requests.post(f"{BASE_URL}/profile/update", headers=HEADERS, json=payload)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
    if response.status_code in [200, 201]:
        print("✅ SUCCESS: Profile updated/created")
        return payload
    else:
        print("❌ FAILURE: Failed to update profile")
        return None

def test_get_profile_existing_user(expected_data):
    print(f"\n--- Testing GET /profile/me for existing user {TEST_USER_ID} ---")
    response = requests.get(f"{BASE_URL}/profile/me", headers=HEADERS)
    print(f"Status Code: {response.status_code}")
    print(f"Response: {json.dumps(response.json(), indent=2)}")
    
    if response.status_code == 200:
        data = response.json()
        matches = True
        for key, value in expected_data.items():
            if data.get(key) != value:
                print(f"❌ MISMATCH: {key} - Expected: {value}, Got: {data.get(key)}")
                matches = False
        
        if matches:
            print("✅ SUCCESS: Retrieved updated profile correctly")
        else:
            print("❌ FAILURE: Profile data mismatch")
    else:
        print("❌ FAILURE: Unexpected status code")

if __name__ == "__main__":
    test_get_profile_new_user()
    updated_data = test_update_profile()
    if updated_data:
        test_get_profile_existing_user(updated_data)
