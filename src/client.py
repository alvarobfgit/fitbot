from datetime import datetime
from http import HTTPStatus
from bs4 import BeautifulSoup
from requests import Session
import logging
import random
import string
from constants import LOGIN_ENDPOINT, book_endpoint, classes_endpoint, ERROR_TAG_ID
from exceptions import BookingFailed, IncorrectCredentials, AlreadyBooked, TooManyWrongAttempts, TooEarly, MESSAGE_BOOKING_FAILED_UNKNOWN, MESSAGE_BOOKING_FAILED_NO_CREDIT, MESSAGE_BOOKING_FAILED_MAX_WAIT_CAPACITY


def _generate_fingerprint(length=50):
    chars = string.ascii_lowercase + string.digits
    return ''.join(random.choices(chars, k=length))


class AimHarderClient:

    def __init__(self, email: str, password: str, box_id: int, box_name: str):
        self.logger = logging.getLogger('aimharder-bot')
        self.box_id = box_id
        self.box_name = box_name
        self.session = self._login(email, password, box_name)

    @staticmethod
    def _login(email: str, password: str, box_name: str):
        session = Session()
        session.headers.update({
            "Accept": "application/json",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Accept-Language": "en-US,en;q=0.9,es;q=0.8,de;q=0.7,ca;q=0.6,it;q=0.5,pt;q=0.4,fr;q=0.3",
            "Content-Type": "application/json",
            "DNT": "1",
            "Origin": "https://login.aimharder.com",
            "Referer": "https://login.aimharder.com/",
            "Priority": "u=1, i",
            "sec-ch-ua": '"Chromium";v="148", "Google Chrome";v="148", "Not/A)Brand";v="99"',
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "sec-fetch-dest": "empty",
            "sec-fetch-mode": "cors",
            "sec-fetch-site": "same-origin",
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
        })

        response = session.post(
            "https://login.aimharder.com/api/login",
            json={
                "username": email,
                "password": password,
                "fingerprint": _generate_fingerprint(),
            },
            allow_redirects=True,
        )
        response.raise_for_status()

        # The login endpoint is on login.aimharder.com but sets amhrdrauth for domain aimharder.com.
        # requests silently drops cross-domain cookies, so we extract it manually from Set-Cookie headers.
        amhrdrauth = None
        for r in response.history + [response]:
            for cookie_header in r.headers.get("Set-Cookie", "").split("\n"):
                if "amhrdrauth=" in cookie_header:
                    amhrdrauth = cookie_header.split("amhrdrauth=")[1].split(";")[0]
                    break

        if not amhrdrauth:
            raise IncorrectCredentials

        session.cookies.set("amhrdrauth", amhrdrauth, domain="aimharder.com")

        # Update session headers for subsequent requests to the gym subdomain
        session.headers.update({
            "Accept": "*/*",
            "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
            "Origin": f"https://{box_name}.aimharder.com",
            "Referer": f"https://{box_name}.aimharder.com/schedule",
            "sec-fetch-site": "same-origin",
        })

        return session

    def get_classes(self, target_day: datetime):
        response = self.session.get(
            classes_endpoint(self.box_name),
            params={
                "box": self.box_id,
                "day": target_day.strftime("%Y%m%d"),
                "familyId": "",
            },
        )
        bookings = response.json().get("bookings")
        self.logger.info(f"Retrieved {len(bookings)} classes for day {target_day.strftime('%Y-%m-%d')}")
        return bookings

    def book_class(self, target_day: datetime, target_class: str) -> bool:
        response = self.session.post(
            book_endpoint(self.box_name),
            headers={
                "Origin": f"https://{self.box_name}.aimharder.com",
                "Referer": f"https://{self.box_name}.aimharder.com/schedule?cl",
                "Priority": "u=1, i",
            },
            data={
                "id": target_class["id"],
                "day": target_day.strftime("%Y%m%d"),
                "insist": 0,
                "familyId": "",
            },
        )
        if response.status_code == HTTPStatus.OK:
            response = response.json()
            if "bookState" in response and response["bookState"] == -1:
                self.logger.error(f"Booking unsuccessful. Max capacity of the waiting list overpassed.")
                raise BookingFailed(MESSAGE_BOOKING_FAILED_MAX_WAIT_CAPACITY)
            
            if "bookState" in response and response["bookState"] == -2:
                self.logger.error(f"Booking unsuccessful. There is no available credits. Max number of booked sessions reached.")
                raise BookingFailed(MESSAGE_BOOKING_FAILED_NO_CREDIT)
            
            if "bookState" in response and response["bookState"] == -12:               
                if response["errorMssgLang"] == "ERROR_ANTELACION_CLIENTE_HORAS":
                    self.logger.error(f"Booking unsuccessful. Too early to book this class.")
                    raise TooEarly(target_day)
                elif response["errorMssgLang"] == "NOPUEDESRESERVAMISMAHORA":
                    self.logger.error(f"Booking unsuccessful. You cannot book the same session twice.")
                    raise AlreadyBooked(target_day)
                
            if "errorMssg" not in response and "errorMssgLang" not in response:
                # booking successful
                self.logger.info(f"Booking completed successfully.")
                return True
            
        self.logger.error(f"UNKNOWN ERROR!!!!!.")
        raise BookingFailed(MESSAGE_BOOKING_FAILED_UNKNOWN)
    
    def cancel_booked_class(self, target_class: str) -> bool:
        response = self.session.post(
            classes_endpoint(self.box_name),
            data={
                "id": target_class["id"],
                "late": 0,
                "familyId": "",
            },
        )
        if response.status_code == HTTPStatus.OK:
            response = response.json()
            if "errorMssg" not in response and "errorMssgLang" not in response:
                # booking cancellation successful
                self.logger.info(f"Booking cancelled successfully.")
                return True
        self.logger.error(f"UNKNOWN ERROR!!!!!.")
        raise BookingFailed(MESSAGE_BOOKING_FAILED_UNKNOWN)
