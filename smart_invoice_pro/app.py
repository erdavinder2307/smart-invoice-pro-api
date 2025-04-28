from flask import Flask
from flasgger import Swagger
from flask_cors import CORS
from smart_invoice_pro.api.routes import auth_blueprint

def create_app():
    app = Flask(__name__)

    # Swagger config (optional)
    app.config['SWAGGER'] = {
        'title': 'Smart Invoice Pro API',
        'uiversion': 3
    }

    Swagger(app)

    # Enable CORS for the Flask app
    CORS(app, resources={r"/*": {"origins": "*"}})

    @app.route('/')
    def home():
        """
        Root endpoint to test Swagger.
        ---
        responses:
          200:
            description: Returns a simple message
        """
        return "Smart Invoice Pro API is running!"

    # Register your API blueprints here
    app.register_blueprint(auth_blueprint, url_prefix="/api")

    return app
