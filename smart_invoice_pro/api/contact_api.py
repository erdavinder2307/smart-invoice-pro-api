import html
import os
from flask import Blueprint, request, jsonify
from flasgger import swag_from
from azure.communication.email import EmailClient

contact_blueprint = Blueprint('contact', __name__)

# Replace with your actual connection string or set it as an environment variable
# export AZURE_EMAIL_CONNECTION_STRING="endpoint=https://<resource>.communication.azure.com/;accesskey=<key>"
CONNECTION_STRING = os.getenv('AZURE_EMAIL_CONNECTION_STRING') or "endpoint=https://<resource>.communication.azure.com/;accesskey=YOUR_KEY"
SENDER_ADDRESS = "admin@solidevelectrosoft.com"
RECIPIENT_ADDRESS = "davinder@solidevelectrosoft.com"

# The route is public, so cap what a stranger can send.
MAX_LENGTHS = {'name': 200, 'email': 320, 'phone': 50, 'subject': 200, 'message': 5000}

@contact_blueprint.route('/contact', methods=['POST'])
@swag_from({
    'consumes': ['application/json'],
    'parameters': [
        {
            'name': 'body',
            'in': 'body',
            'required': True,
            'schema': {
                'type': 'object',
                'properties': {
                    'name': {'type': 'string'},
                    'email': {'type': 'string'},
                    'phone': {'type': 'string'},
                    'subject': {'type': 'string'},
                    'message': {'type': 'string'}
                },
                'required': ['name', 'email', 'subject', 'message']
            },
            'description': 'Contact form data'
        }
    ],
    'responses': {
        '200': {
            'description': 'Message sent successfully'
        },
        '400': {
            'description': 'Invalid input'
        },
        '500': {
            'description': 'Failed to send email'
        }
    }
})
def send_message():
    if not request.is_json:
        return jsonify({"error": "Request must be JSON"}), 400
    
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"error": "Request must be a JSON object"}), 400
    
    # Validation
    required_fields = ['name', 'email', 'subject', 'message']
    for field in required_fields:
        if field not in data or not data[field]:
             return jsonify({"error": f"Field '{field}' is required"}), 400
    for field, max_len in MAX_LENGTHS.items():
        value = data.get(field)
        if value is not None and (not isinstance(value, str) or len(value) > max_len):
            return jsonify({"error": f"Field '{field}' must be text of at most {max_len} characters"}), 400

    name = data['name']
    email = data['email']
    phone = data.get('phone') or 'N/A'
    # No line breaks in the subject line of the email.
    subject = ' '.join(data['subject'].splitlines())
    message_content = data['message']

    print("New contact message received")

    try:
        if not CONNECTION_STRING or "YOUR_KEY" in CONNECTION_STRING:
             print("WARNING: Azure Connection String not configured. Email will NOT be sent.")
             return jsonify({"message": "Message received (Email simulation only - invalid key)!"}), 200

        client = EmailClient.from_connection_string(CONNECTION_STRING)

        safe = {k: html.escape(v) for k, v in
                {'name': name, 'email': email, 'phone': phone, 'message': message_content}.items()}
        email_message = {
            "senderAddress": SENDER_ADDRESS,
            "recipients": {
                "to": [{"address": RECIPIENT_ADDRESS}],
            },
            "content": {
                "subject": f"New Contact Request: {subject}",
                "plainText": f"Name: {name}\nEmail: {email}\nPhone: {phone}\n\nMessage:\n{message_content}",
                "html": f"""
                <html>
                    <body>
                        <h1>New Contact Request</h1>
                        <p><strong>Name:</strong> {safe['name']}</p>
                        <p><strong>Email:</strong> {safe['email']}</p>
                        <p><strong>Phone:</strong> {safe['phone']}</p>
                        <br>
                        <h2>Message:</h2>
                        <p>{safe['message']}</p>
                    </body>
                </html>
                """
            }
        }

        poller = client.begin_send(email_message)
        result = poller.result()
        print(f"Email sent successfully. Message ID: {result['id']}")
        
        return jsonify({"message": "Message sent successfully!"}), 200

    except Exception as e:
        print(f"Error sending contact email: {type(e).__name__}")
        return jsonify({"error": "Failed to send your message. Please try again later."}), 500
