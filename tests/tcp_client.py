"""Тестовая программа: подключается к TCP-серверу и обменивается сообщением."""

import socket
import sys


def main():
    host, port = sys.argv[1], int(sys.argv[2])
    with socket.create_connection((host, port), timeout=5) as connection:
        connection.sendall(b"ping")
        return 0 if connection.recv(16) == b"pong" else 1


if __name__ == "__main__":
    raise SystemExit(main())
