"""Send one syslog line to the receiver: python tools/send_syslog.py P1 LINK_DOWN [host] [port]"""
import socket, sys
dev, typ = sys.argv[1], sys.argv[2]
host, port = (sys.argv[3] if len(sys.argv) > 3 else "127.0.0.1"), int(sys.argv[4] if len(sys.argv) > 4 else 5140)
socket.socket(socket.AF_INET, socket.SOCK_DGRAM).sendto(("<14>%s %s demo event" % (dev, typ)).encode(), (host, port))
