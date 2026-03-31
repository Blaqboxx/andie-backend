from ...policy.validator import validate_code

safe_code = "print('hello world')"
bad_code = "import os"

print("SAFE TEST:", validate_code(safe_code))
print("BAD TEST:", validate_code(bad_code))

