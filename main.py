import os
from streamlit.web import bootstrap


def main():
    port = int(os.environ.get("PORT", 8501))
    address = "0.0.0.0"
    bootstrap.run("app.py", False, [], {"server.port": port, "server.address": address})


if __name__ == "__main__":
    main()
