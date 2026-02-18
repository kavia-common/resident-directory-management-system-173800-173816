#!/bin/bash
cd /home/kavia/workspace/code-generation/resident-directory-management-system-173800-173816/resident_directory_backend
source venv/bin/activate
flake8 .
LINT_EXIT_CODE=$?
if [ $LINT_EXIT_CODE -ne 0 ]; then
  exit 1
fi

