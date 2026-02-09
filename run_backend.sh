#!/bin/bash

# Navigate to the script's directory
cd "$(dirname "$0")"

# API Configuration
PORT=5001

echo "Starting Backend API on port $PORT..."

# Check if venv exists
if [ -d "venv" ]; then
    source venv/bin/activate
else
    echo "Virtual environment not found. Please create it first."
    exit 1
fi

# Run the application
python3 main.py
