#!/bin/bash

# Export data from local database
echo "Exporting data..."
python manage.py dumpdata app_name.ModelName --output data.json

echo "Data exported to data.json"

# Add, commit, and push changes to Git
echo "Adding changes to Git..."
git add data.json
git commit -m "Update data export"
git push

echo "Changes pushed to Git repository."
