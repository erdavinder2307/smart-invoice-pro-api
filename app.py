from flask import render_template

from smart_invoice_pro.app import create_app

# app = Flask(__name__)
app = create_app()

@app.route('/')
def index():
    return render_template("index.html") 

if __name__=='__main__':
    app.run(debug=True)