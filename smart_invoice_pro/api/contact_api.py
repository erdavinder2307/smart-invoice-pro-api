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
    
    data = request.get_json()
    
    # Validation
    required_fields = ['name', 'email', 'subject', 'message']
    for field in required_fields:
        if field not in data or not data[field]:
             return jsonify({"error": f"Field '{field}' is required"}), 400

    name = data['name']
    email = data['email']
    phone = data.get('phone', 'N/A')
    subject = data['subject']
    message_content = data['message']

    print(f"------------ NEW CONTACT MESSAGE ------------")
    print(f"Name: {name}")
    print(f"Email: {email}")
    print(f"Phone: {phone}")
    print(f"Subject: {subject}")
    print(f"Message: {message_content}")
    print(f"---------------------------------------------")

    try:
        if not CONNECTION_STRING or "YOUR_KEY" in CONNECTION_STRING:
             print("WARNING: Azure Connection String not configured. Email will NOT be sent.")
             return jsonify({"message": "Message received (Email simulation only - invalid key)!"}), 200

        client = EmailClient.from_connection_string(CONNECTION_STRING)

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
                        <p><strong>Name:</strong> {name}</p>
                        <p><strong>Email:</strong> {email}</p>
                        <p><strong>Phone:</strong> {phone}</p>
                        <br>
                        <h2>Message:</h2>
                        <p>{message_content}</p>
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
        print(f"Error sending email: {str(e)}")
        # We might still want to return 200 to the frontend if we logged it, 
        # or 500 if we want to bubble up the error. 
        # For a contact form, it's often better to fail gracefully if the DB/Log worked (which we did above) 
        # but here we rely solely on email so let's return error if email fails.
        return jsonify({"error": f"Failed to send email: {str(e)}"}), 500
