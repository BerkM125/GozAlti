#!/usr/bin/env python3
"""Check Twilio credentials WITHOUT placing a call.

Trial accounts fail in one specific, unhelpful way: you can dial only numbers you have
verified in the console, and the refusal arrives as a generic 21210/21219 error at the
moment you place the call — which, on a demo, is the moment you cannot afford it. This
reads the account, the from-numbers you own, and the caller IDs you have verified, so
the failure surfaces now instead of on camera.

Read-only. It never places a call.
"""
import base64
import json
import os
import sys
import urllib.error
import urllib.request

SID = os.environ.get("TWILIO_SID", "")
TOKEN = os.environ.get("TWILIO_TOKEN", "")
FROM = os.environ.get("TWILIO_FROM", "")
TO = os.environ.get("CALLING_CONTACT_NUMBER", "")


def get(path):
    url = f"https://api.twilio.com/2010-04-01/Accounts/{SID}/{path}"
    auth = base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()
    req = urllib.request.Request(url, headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            return json.loads(r.read())
    except urllib.error.HTTPError as e:
        return {"_error": f"HTTP {e.code}: {e.read()[:200].decode('utf-8','replace')}"}
    except Exception as e:
        return {"_error": f"{type(e).__name__}: {e}"}


def main():
    if not (SID and TOKEN):
        print("TWILIO_SID / TWILIO_TOKEN not set — put them in .env")
        return 1

    auth = base64.b64encode(f"{SID}:{TOKEN}".encode()).decode()
    req = urllib.request.Request(f"https://api.twilio.com/2010-04-01/Accounts/{SID}.json",
                                 headers={"Authorization": f"Basic {auth}"})
    try:
        with urllib.request.urlopen(req, timeout=15) as r:
            acct = json.loads(r.read())
    except urllib.error.HTTPError as e:
        print(f"credentials rejected — HTTP {e.code}: "
              f"{e.read()[:200].decode('utf-8','replace')}")
        return 1

    status, typ = acct.get("status"), acct.get("type")
    print(f"account : {acct.get('friendly_name')}  [{typ}, {status}]")
    if typ == "Trial":
        print("          TRIAL — may only dial VERIFIED numbers, and every call is")
        print("          prefixed with Twilio's own trial announcement.")

    owned = [n["phone_number"] for n in get("IncomingPhoneNumbers.json").get("incoming_phone_numbers", [])]
    print(f"from    : {owned or 'NONE — buy a number, trial credit covers it'}")
    if FROM and FROM not in owned:
        print(f"          !! TWILIO_FROM={FROM} is not a number on this account")

    verified = [c["phone_number"] for c in get("OutgoingCallerIds.json").get("outgoing_caller_ids", [])]
    print(f"verified: {verified or 'NONE'}")
    if typ == "Trial" and TO and TO not in verified:
        print(f"          !! CALLING_CONTACT_NUMBER={TO} is NOT verified — the call will")
        print("             be rejected. Console -> Phone Numbers -> Verified Caller IDs.")

    ok = bool(owned) and (typ != "Trial" or not TO or TO in verified)
    print("\nready to ring." if ok else "\nnot ready — fix the !! lines above.")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
