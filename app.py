
from flask import Flask, request, jsonify
from flask_cors import CORS
import requests
import firebase_admin
from firebase_admin import credentials, firestore
from firebase_admin import firestore as admin_firestore
from datetime import datetime, timedelta, timezone
import os
import base64
import json
import time
import random


app = Flask(__name__)
CORS(app)


# ============================================================
# Configuration
# ============================================================

WHATSAPP_TOKEN = os.environ.get('WHATSAPP_TOKEN', 'EAAQnezZAE2U4BP2TbN9TOdX4pDseOr7APww3HsDAcZCbT1ZBnBac9bCe5Qz7eVFITvcZBOBa1ibHb39dPnsKCvdSppdVALaEM2bcn5cNpDTnX4iOtVwOE7QZC9P59xLhTC4aQ2Kwz2Lfl9792jC8ywvrRLK3PxFNN1czlDlHRcZBZCVLi27QzrioYVKAUtmJwZDZD')
PHONE_NUMBER_ID = os.environ.get('PHONE_NUMBER_ID', '913694458491714')
VERIFY_TOKEN = os.environ.get('VERIFY_TOKEN', 'zest_rewards_webhook_2025')
RESTAURANT_ID = os.environ.get('RESTAURANT_ID', 'rest_001')


# ============================================================
# Initialize Firebase
# ============================================================

try:
    firebase_creds_base64 = os.environ.get('FIREBASE_CREDENTIALS_BASE64')

    if firebase_creds_base64:
        print("🔐 Using Firebase credentials from environment variable")
        cred_json = base64.b64decode(firebase_creds_base64)
        cred_dict = json.loads(cred_json)
        cred = credentials.Certificate(cred_dict)
    else:
        print("📁 Using Firebase credentials from file")
        cred = credentials.Certificate("firebase-credentials.json")

    firebase_admin.initialize_app(cred)
    db = firestore.client()
    print("✅ Firebase connected!")
except Exception as e:
    print(f"❌ Firebase error: {e}")
    db = None


# ============================================================
# Helper Functions
# ============================================================

def clean_phone_number(phone):
    """Remove + sign and clean phone number"""
    if not phone:
        return None
    cleaned = phone.replace('+', '').replace(' ', '').replace('-', '')
    return cleaned


def send_text(to_number, message, restaurant_id=None):
    """
    Send WhatsApp text message using global credentials.

    Args:
        to_number: Customer phone number
        message: Text message body
        restaurant_id: For logging/audit only (not used for credentials)

    Returns:
        Dict with response data or error
    """
    clean_number = clean_phone_number(to_number)

    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "text",
        "text": {"body": message}
    }

    # Retry logic: 3 attempts with exponential backoff
    max_retries = 3
    base_delay = 1  # seconds

    for attempt in range(max_retries):
        try:
            response = requests.post(url, json=payload, headers=headers, timeout=10)

            print(f"📤 [Attempt {attempt + 1}] Sent to {clean_number}: {response.status_code}")

            if response.status_code == 200:
                result = response.json()
                return result

            print(f"❌ WhatsApp API Error: {response.text}")

            # Don't retry client errors (4xx except rate limits)
            if 400 <= response.status_code < 500 and response.status_code != 429:
                return {"error": response.text}

            # Retry on 5xx or 429 (rate limit)
            if attempt < max_retries - 1:
                delay = base_delay * (2 ** attempt)
                print(f"⏳ Retrying in {delay}s...")
                time.sleep(delay)
            else:
                return {"error": response.text}

        except requests.exceptions.Timeout:
            print(f"⏱️ Timeout on attempt {attempt + 1}")
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
            else:
                return {"error": "Request timeout"}

        except requests.exceptions.RequestException as e:
            print(f"🔌 Network error: {e}")
            if attempt < max_retries - 1:
                time.sleep(base_delay * (2 ** attempt))
            else:
                return {"error": f"Network error: {str(e)}"}

    return {"error": "Max retries exceeded"}


def send_template_message(phone_number, template_name, params):
    """Send WhatsApp template message (no 24-hour limit)"""
    clean_number = clean_phone_number(phone_number)

    url = f"https://graph.facebook.com/v21.0/{PHONE_NUMBER_ID}/messages"
    headers = {
        "Authorization": f"Bearer {WHATSAPP_TOKEN}",
        "Content-Type": "application/json"
    }

    payload = {
        "messaging_product": "whatsapp",
        "to": clean_number,
        "type": "template",
        "template": {
            "name": template_name,
            "language": {"code": "en"},
            "components": [
                {
                    "type": "body",
                    "parameters": [{"type": "text", "text": p} for p in params]
                }
            ]
        }
    }

    response = requests.post(url, json=payload, headers=headers)
    print(f"[TEMPLATE SEND] → {clean_number}: {response.status_code}")
    return response.json()


def get_customers_by_segment(segment, restaurant_id=None):
    """
    Get customers based on segment type

    Segments:
    - all: All customers
    - recent: Registered in last 30 days
    - older: Registered 30+ days ago
    """
    if not db:
        return []

    rest_id = restaurant_id or RESTAURANT_ID

    try:
        if segment == 'recent':
            thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
            customers_ref = db.collection('customers')\
                .where('restaurant_id', '==', rest_id)\
                .where('registered_at', '>=', thirty_days_ago)\
                .stream()

        elif segment == 'older':
            thirty_days_ago = datetime.now(timezone.utc) - timedelta(days=30)
            customers_ref = db.collection('customers')\
                .where('restaurant_id', '==', rest_id)\
                .where('registered_at', '<', thirty_days_ago)\
                .stream()

        else:  # 'all' or default
            customers_ref = db.collection('customers')\
                .where('restaurant_id', '==', rest_id)\
                .stream()

        # Convert to list
        customers = []
        for customer in customers_ref:
            customers.append(customer.to_dict())

        return customers

    except Exception as e:
        print(f"❌ Error getting customers: {e}")
        return []


# ============================================================
# Onboarding Functions
# ============================================================

def get_customer_by_phone_only(phone_number, restaurant_id):
    """Get customer by phone and restaurant"""
    if not db:
        return None

    phone = clean_phone_number(phone_number)
    customer_id = f"{phone}_{restaurant_id}"
    customer_ref = db.collection('customers').document(customer_id)
    customer = customer_ref.get()

    if customer.exists:
        print(f"✅ Found existing customer: {customer_id}")
        return customer.to_dict()

    print(f"ℹ️ No customer found: {customer_id}")
    return None


def get_restaurant_code(restaurant_id):
    """Get active signup code for restaurant"""
    if not db:
        print("❌ Database not connected")
        return None

    try:
        rest_doc = db.collection('restaurants').document(restaurant_id).get()

        if rest_doc.exists:
            code = rest_doc.to_dict().get('signup_code')
            print(f"✅ Restaurant code found: {code}")
            return code
        else:
            print(f"⚠️ No code set for restaurant: {restaurant_id}")
            return None
    except Exception as e:
        print(f"❌ Error getting restaurant code: {e}")
        return None


def validate_signup_code(code_entered, restaurant_id):
    """Check if entered code matches restaurant's active code"""
    active_code = get_restaurant_code(restaurant_id)

    if not active_code:
        return False, "No active code set for this restaurant"

    if code_entered.upper().strip() != active_code.upper().strip():
        print(f"❌ Code mismatch: entered '{code_entered}' vs active '{active_code}'")
        return False, "Invalid code"

    print(f"✅ Code validated: {code_entered}")
    return True, "Valid"


def get_signup_reward(code, restaurant_id):
    """Get reward for signup code with random probability check"""
    if not db:
        return None

    try:
        reward_id = f"{code.upper()}_{restaurant_id}"
        reward_ref = db.collection('signup_rewards').document(reward_id)
        reward_snap = reward_ref.get()

        if not reward_snap.exists:
            print(f"⚠️ No reward configured for code: {code}")
            return None

        reward_data = reward_snap.to_dict()

        # Check if active
        if reward_data.get('status') != 'active':
            print(f"⚠️ Reward is not active: {code}")
            return None

        # Random probability check
        win_probability = reward_data.get('win_probability', 0.5)  # Default 50%
        random_number = random.random()  # Generates 0.0 to 1.0

        print(f"🎲 Random check: {random_number:.2f} vs {win_probability:.2f}")

        if random_number < win_probability:
            # WINNER!
            print(f"✅ WINNER! Customer gets reward: {code}")
            return reward_data
        else:
            # No luck this time
            print(f"❌ No luck. Customer doesn't get reward: {code}")
            return None

    except Exception as e:
        print(f"❌ Error getting reward: {e}")
        return None


def increment_reward_usage(code, restaurant_id):
    """Track reward winners and total attempts"""
    if not db:
        return False

    try:
        reward_id = f"{code.upper()}_{restaurant_id}"
        reward_ref = db.collection('signup_rewards').document(reward_id)

        # Increment winner count and total attempts
        reward_ref.update({
            'total_winners': admin_firestore.Increment(1),
            'total_attempts': admin_firestore.Increment(1),
            'last_won_at': datetime.now(timezone.utc)
        })

        print(f"✅ Reward stats updated for: {code}")
        return True

    except Exception as e:
        print(f"❌ Error updating reward stats: {e}")
        return False


def track_reward_attempt(code, restaurant_id):
    """Track when someone attempts but doesn't win"""
    if not db:
        return False

    try:
        reward_id = f"{code.upper()}_{restaurant_id}"
        reward_ref = db.collection('signup_rewards').document(reward_id)

        # Only increment attempts, not winners
        reward_ref.update({
            'total_attempts': admin_firestore.Increment(1)
        })

        return True

    except Exception as e:
        print(f"❌ Error tracking attempt: {e}")
        return False


def create_onboarding_customer(phone_number, code, restaurant_id):
    """Create new customer (no reward stored in customer doc)"""
    if not db:
        print("❌ Database not connected")
        return False, None
    
    # Check for reward with random probability
    reward_data = get_signup_reward(code, restaurant_id)
    
    now = datetime.now(timezone.utc)
    phone_clean = clean_phone_number(phone_number)
    customer_id = f"{phone_clean}_{restaurant_id}"
    
    try:
        # Create customer document (SAME for everyone)
        customer_doc = {
            'phone_number': phone_clean,
            'restaurant_id': restaurant_id,
            'registered_at': now,
            'signup_code': code,
            'status': 'active',
            'onboarding_source': 'QR_CODE'
        }
        
        # Track stats only
        if reward_data:
            increment_reward_usage(code, restaurant_id)
        else:
            track_reward_attempt(code, restaurant_id)
        
        db.collection('customers').document(customer_id).set(customer_doc)
        
        db.collection('restaurants').document(restaurant_id).update({
            'total_signups': admin_firestore.Increment(1)
        })
        
        print(f"✅ Customer created: {customer_id} | Won: {reward_data is not None}")
        return True, reward_data
        
    except Exception as e:
        print(f"❌ Error: {e}")
        return False, None


# ============================================================
# Flask Routes
# ============================================================

@app.route('/')
def home():
    return "✅ ZestRewards API is running!"


@app.route('/webhook', methods=['GET'])
def verify_webhook():
    """Verify webhook for WhatsApp"""
    mode = request.args.get('hub.mode')
    token = request.args.get('hub.verify_token')
    challenge = request.args.get('hub.challenge')

    if mode == 'subscribe' and token == VERIFY_TOKEN:
        print("✅ Webhook verified")
        return challenge, 200
    else:
        print("❌ Webhook verification failed")
        return "Forbidden", 403


@app.route('/webhook', methods=['POST'])
def receive_message():
    """Receive messages from Meta WhatsApp - Onboarding Flow"""
    data = request.get_json()

    print("=" * 60)
    print("📨 Webhook received")
    print("=" * 60)

    try:
        value = data['entry'][0]['changes'][0]['value']

        # Verify this is our phone number
        metadata = value.get('metadata', {})
        incoming_phone_id = metadata.get('phone_number_id')

        if incoming_phone_id and incoming_phone_id != PHONE_NUMBER_ID:
            print(f"⚠️ Message for different phone_number_id: {incoming_phone_id}")
            print(f"   Expected: {PHONE_NUMBER_ID}")

        if 'messages' in value:
            message = value['messages'][0]
            from_number = clean_phone_number(message['from'])

            if 'text' in message:
                text = message['text']['body']
                print(f"📱 From: {from_number}")
                print(f"💬 Message: {text}")

                text_clean = text.strip()

                # Check if customer exists
                print(f"🔍 Checking customer...")
                customer = get_customer_by_phone_only(from_number, RESTAURANT_ID)

                if customer:
                    print(f"✅ Customer exists")
                else:
                    print(f"ℹ️ New customer - not in database")

                # ========================================
                # CASE 1: Existing customer
                # ========================================
                if customer:
                    print(f"✅ CASE 1: Existing registered customer")

                    # Check if they sent a signup code
                    print(f"🔐 Checking if message is signup code...")
                    is_valid_code, _ = validate_signup_code(text_clean, RESTAURANT_ID)

                    if is_valid_code:
                        # Check if customer already signed up with THIS EXACT CODE
                        customer_signup_code = customer.get('signup_code', '').upper()
                        entered_code = text_clean.upper()

                        if customer_signup_code == entered_code:
                            # Customer trying to use the SAME code again
                            print(f"⚠️ Customer already used this exact code: {entered_code}")
                            message_text = """You've already used this code! ✅

You're registered. Watch out for exclusive offers coming soon! 🎁"""

                            print("📤 Sending 'already used' message")
                            send_text(from_number, message_text, RESTAURANT_ID)
                            print("✅ Message sent successfully!")
                            return jsonify({"status": "ok"}), 200
                        else:
                            # Customer entered a DIFFERENT valid code - treat as new signup!
                            print(f"🆕 Customer entered NEW code. Old: {customer_signup_code}, New: {entered_code}")
                            print(f"   → Treating as new signup with new code")

                            # Create customer and check for reward with NEW code
                            success, reward_data = create_onboarding_customer(
                                from_number, 
                                text_clean.upper(), 
                                RESTAURANT_ID
                            )

                            if success:
                                if reward_data:
                                    # Customer got a reward with new code!
                                    reward_desc = reward_data['reward_description']
                                    message_text = f"""🎉 New code registered!

🎁 SPECIAL REWARD: {reward_desc}

Show this message to the cashier to claim your reward!

We'll keep sending you exclusive offers. Stay tuned! 📲"""
                                else:
                                    # No reward with new code
                                    message_text = """🎉 New code registered!

You're all set! We'll keep sending you exclusive offers and updates.
Stay tuned! 📲"""

                                print("📤 Sending new code welcome message")
                                send_text(from_number, message_text, RESTAURANT_ID)
                                print("✅ Message sent successfully!")
                            else:
                                print("❌ Failed to update customer with new code")
                                send_text(from_number, "Sorry, registration failed. Please try again later.", RESTAURANT_ID)

                            return jsonify({"status": "ok"}), 200
                    else:
                        # Not a signup code - just a random message
                        print(f"ℹ️ Customer sent random message: '{text_clean}'")
                        message_text = """Thanks for your message! 👋

We'll keep you updated with exclusive offers soon! 🎁

Need help? Contact our staff or visit us! 😊"""

                        print("📤 Sending response to existing customer")
                        send_text(from_number, message_text, RESTAURANT_ID)
                        print("✅ Message sent successfully!")
                        return jsonify({"status": "ok"}), 200

                # ========================================
                # CASE 2: New customer - validate signup code
                # ========================================
                else:
                    print(f"🆕 CASE 2: New customer attempting signup")

                    # Validate signup code
                    print(f"🔐 Validating code: '{text_clean}'")
                    is_valid, validation_message = validate_signup_code(text_clean, RESTAURANT_ID)
                    print(f"   Validation result: {is_valid} - {validation_message}")

                    if is_valid:
                        print(f"✅ Valid code! Creating customer...")

                        # Create customer and check for reward
                        success, reward_data = create_onboarding_customer(
                            from_number, 
                            text_clean.upper(), 
                            RESTAURANT_ID
                        )

                        if success:
                            if reward_data:
                                # Customer got a reward!
                                reward_desc = reward_data['reward_description']
                                message_text = f"""🎉 Welcome! You're registered!

🎁 SPECIAL REWARD: {reward_desc}

Show this message to the cashier to claim your reward!

We'll also send you exclusive offers. Stay tuned! 📲"""
                            else:
                                # No reward
                                message_text = """🎉 Welcome to our exclusive club!

You're all set! We'll send you exclusive offers and updates soon.
Stay tuned! 📲"""

                            print("📤 Sending welcome message")
                            send_text(from_number, message_text, RESTAURANT_ID)
                            print("✅ Message sent successfully!")
                        else:
                            print("❌ Failed to create customer")
                            send_text(from_number, "Sorry, registration failed. Please try again later.", RESTAURANT_ID)
                    else:
                        print(f"❌ Invalid code entered")
                        message_text = """❌ Invalid code.

Please ask the cashier for the correct signup code."""
                        print("📤 Sending invalid code message")
                        send_text(from_number, message_text, RESTAURANT_ID)
                        print("✅ Message sent successfully!")

                    return jsonify({"status": "ok"}), 200

        elif 'statuses' in value:
            status = value['statuses'][0]
            print(f"📊 Status update: {status.get('status')}")
            return jsonify({"status": "ok"}), 200

    except Exception as e:
        print(f"❌ ERROR in webhook: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({"status": "error", "message": str(e)}), 500

    return jsonify({"status": "ok"}), 200


@app.route('/send-template-campaign', methods=['POST'])
def send_template_campaign():
    """
    Send campaign using WhatsApp TEMPLATE messages (No 24hr limit)

    Request body:
    {
        "segment": "all|recent|older",
        "template_name": "your_template_name",
        "restaurant_id": "rest_001" (optional)
    }

    Template variables will be filled automatically:
    {{1}} = Restaurant name (from Firestore)
    """

    data = request.get_json()
    if not data:
        return jsonify({"error": "Missing request body"}), 400

    segment = data.get("segment", "all")
    restaurant_id = data.get("restaurant_id", RESTAURANT_ID)
    template_name = data.get("template_name")

    if not template_name:
        return jsonify({"error": "template_name is required"}), 400

    # Get restaurant name from Firestore
    rest_doc = db.collection('restaurants').document(restaurant_id).get()
    if rest_doc.exists:
        restaurant_name = rest_doc.to_dict().get('restaurant_name', "Our Restaurant")
    else:
        restaurant_name = "Our Restaurant"

    # Get customers
    customers = get_customers_by_segment(segment, restaurant_id)
    total = len(customers)

    if total == 0:
        return jsonify({"success": False, "message": "No customers found"}), 200

    sent, failed = 0, 0

    for cust in customers:
        try:
            # Build params for template
            params = [restaurant_name]  # {{1}}

            # Send template message
            result = send_template_message(
                cust["phone_number"],
                template_name,
                params
            )

            if "messages" in result:
                sent += 1
            else:
                failed += 1

        except Exception as err:
            print("Error sending:", err)
            failed += 1

    return jsonify({
        "success": True,
        "template": template_name,
        "segment": segment,
        "restaurant_name": restaurant_name,
        "total_customers": total,
        "sent": sent,
        "failed": failed
    }), 200


# ============================================================
# Run Flask App
# ============================================================

if __name__ == '__main__':
    port = int(os.environ.get('PORT', 5000))
    app.run(host='0.0.0.0', port=port, debug=False)
