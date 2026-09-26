"""Compatibility entry point; the backend now runs weekly."""
from weekly import classify_pending, run

if __name__ == "__main__":
    run()
