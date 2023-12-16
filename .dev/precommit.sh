#!/bin/bash

cd ..

echo "Linting..."
pylint --output-format=colorized --enable spelling -j 8 ./**/*.py