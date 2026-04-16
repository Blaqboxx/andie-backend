#!/bin/bash
# Build and test script for ANDIE backend

set -e

# 1. Install Python dependencies
pip install -r requirements.txt

# 2. Run Python tests
python3 -m unittest discover -s tests

# 3. (Optional) Build frontend if present
if [ -f "../andie-ui/package.json" ]; then
  echo "Building frontend..."
  cd ../andie-ui
  npm install
  npm run build
  cd -
fi

echo "Build and tests completed successfully."
