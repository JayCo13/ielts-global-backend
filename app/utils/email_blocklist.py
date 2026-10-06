"""Blocklist of disposable / temporary email domains.

Used at registration to reject throwaway inboxes (10minutemail, mailinator, …)
that pass MX + OTP checks but exist only to bypass verification. Not exhaustive
— covers the most common providers and their many alias domains. Extend
DISPOSABLE_DOMAINS as new ones show up in signups.
"""

# Lowercase, bare domains (no leading @).
DISPOSABLE_DOMAINS = {
    # 10minutemail family
    "10minutemail.com", "10minutemail.net", "10minemail.com", "10minutemail.co.uk",
    "20minutemail.com", "30minutemail.com",
    # mailinator family
    "mailinator.com", "mailinator.net", "mailinator2.com", "mailinator.org",
    "reallymymail.com", "sogetthis.com", "spamherelots.com", "notmailinator.com",
    # guerrillamail family
    "guerrillamail.com", "guerrillamail.net", "guerrillamail.org", "guerrillamail.biz",
    "guerrillamail.de", "guerrillamailblock.com", "grr.la", "sharklasers.com",
    "spam4.me", "pokemail.net",
    # temp-mail / tempmail family
    "temp-mail.org", "temp-mail.com", "tempmail.com", "tempmail.net", "tempmailo.com",
    "tempr.email", "tempmailaddress.com", "tempmail.plus", "tmail.ws", "tempinbox.com",
    # yopmail
    "yopmail.com", "yopmail.net", "yopmail.fr", "cool.fr.nf", "jetable.fr.nf",
    "nospam.ze.tc", "nomail.xl.cx", "mega.zik.dj", "speed.1s.fr", "courriel.fr.nf",
    "moncourrier.fr.nf", "monemail.fr.nf", "monmail.fr.nf",
    # getnada / nada
    "getnada.com", "nada.email", "nada.ltd",
    # dropmail / mailnesia / etc
    "dropmail.me", "mailnesia.com", "mailcatch.com", "trashmail.com", "trashmail.net",
    "trashmail.me", "wegwerfmail.de", "wegwerfmail.net", "wegwerfmail.org",
    "trbvm.com", "trbvn.com",
    # throwawaymail / fakeinbox / mytemp
    "throwawaymail.com", "fakeinbox.com", "fakemailgenerator.com", "mytemp.email",
    "tempemail.co", "tempemails.io", "email-fake.com", "fake-mail.ml", "fakemail.net",
    # maildrop / mohmal / etc
    "maildrop.cc", "mohmal.com", "mohmal.in", "mailsac.com", "harakirimail.com",
    "inboxbear.com", "inboxkitten.com", "burnermail.io", "33mail.com",
    # dispostable / spambox / others
    "dispostable.com", "spambox.us", "spamgourmet.com", "mailexpire.com",
    "incognitomail.org", "getairmail.com", "throwam.com", "mvrht.net", "mailismagic.com",
    # minute/second mails and common VN-seen throwaways
    "1secmail.com", "1secmail.net", "1secmail.org", "esiix.com", "wwjmp.com",
    "xojxe.com", "yoggm.com", "kzccv.com", "qiott.com", "vjuum.com", "laafd.com",
    "txcct.com", "27email.com", "emltmp.com",
    # misc widely-used
    "moakt.com", "moakt.cc", "moakt.ws", "tafmail.com", "disbox.net", "vusra.com",
    "cs.email", "clrmail.com", "byom.de", "discard.email", "discardmail.com",
    "trash-mail.com", "kurzepost.de", "objectmail.com", "proxymail.eu",
    "rcpt.at", "trash2009.com", "mt2015.com", "thankyou2010.com",
}


def is_disposable_email(email: str) -> bool:
    """True if the email's domain is a known disposable/temporary provider."""
    if not email or "@" not in email:
        return False
    domain = email.rsplit("@", 1)[1].strip().lower().rstrip(".")
    return domain in DISPOSABLE_DOMAINS
