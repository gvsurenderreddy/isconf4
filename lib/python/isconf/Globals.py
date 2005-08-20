import os
import re
import sys

# Environment variables:
#
# VARISCONF     dynamic data dir, defaults to /var/isconf
# GNUPGHOME     used only for user keys, not host keys
# 

verbose = False

# return codes/messages 
SHORT_READ        = (54, "message truncated, data contains missing byte count")
BAD_RECORD        = (95, "bad record")
INVALID_RECTYPE   = (96, "invalid record type")
PROTOCOL_MISMATCH = (97, "protocol mismatch")

RE = {
    'newline': '\n',
    'size': '(\d+)\s*\n',
    # 'pgpkey': '-----BEGIN PGP PUBLIC KEY BLOCK-----\n',
    # 'pgpmessage': '-----BEGIN PGP MESSAGE-----\n',
    # 'headers': '-----BEGIN ((.|\n)*?)\n\n((.|\n)*?)\n\n',
    'headbody': '^\n*((.|\n)*?\n)\n([\s\S]*)$',
}

# replace uncompiled patterns with compiled ones
for (name,expr) in RE.items():
    RE[name] = re.compile(expr)

# try to make 2.2's old booleans look like 2.3+ so doctest output is
# consistent
# if str(True) is '1':
#     class _True:
#         def __str__(self): return 'True'
#         def __int__(self): return 1
#     class _False:
#         def __str__(self): return 'False'
#         def __int__(self): return 0
#     True = _True()
#     False = _False()
# 
# print True 
# print False 
# 
# if True: print "True ok"
# if False: print "False bad"

def info(*msg):
    if not os.environ['VERBOSE']:
        return
    error(*msg)
def error(*msg):
    for m in msg:
        print >>sys.stderr, m
    print >>sys.stderr, "\n"
def panic(*msg):
    error(*msg)
    sys.exit(99)

