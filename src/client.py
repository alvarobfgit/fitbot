import time
from datetime import datetime
from http import HTTPStatus
from typing import Optional

from bs4 import BeautifulSoup
from requests import Session
from requests.exceptions import JSONDecodeError

from constants import (
    LOGIN_ENDPOINT,
    login_endpoint,
    book_endpoint,
    classes_endpoint,
    ERROR_TAG_ID,
)
from exceptions import (
    BookingFailed,
    IncorrectCredentials,
    TooManyWrongAttempts,
    MESSAGE_BOOKING_FAILED_UNKNOWN,
    MESSAGE_BOOKING_FAILED_NO_CREDIT,
    MESSAGE_TOO_SOON_TO_BOOK,
)
from logger import logger


class AimHarderClient:
    BOOKING_CONFIRMATION_RETRIES = 3
    BOOKING_CONFIRMATION_DELAY_SECONDS = 1
    BOOKING_REQUEST_RETRIES = 2
    BROWSER_USER_AGENT = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/148.0.0.0 Safari/537.36 Edg/148.0.0.0"
    )

    def __init__(
        self,
        email: str,
        password: str,
        box_id: int,
        box_name: str,
        proxy: Optional[str] = None,
    ):
        self.email = email
        self.password = password
        self.proxy = proxy
        self.base_url = f"https://{box_name}.aimharder.com"
        self.box_id = box_id
        self.box_name = box_name
        self.session = self._login(email, password, proxy, box_name)
        self._init_box_session()

    def _init_box_session(self):
        """Visit the gym's schedule page to establish a valid subdomain session.
        The browser always loads this page before making API calls, which sets
        the necessary auth cookie for the subdomain.
        """
        try:
            self.session.get(
                f"{self.base_url}/schedule",
                headers={"User-Agent": self.BROWSER_USER_AGENT},
            )
            logger.info("Box session initialized")
        except Exception as e:
            logger.warning("Box session initialization failed: %s", e)

    def _ensure_cookie_alive(self):
        """Mirror the browser keep-alive flow used before privileged actions.
        The web app checks /cookiealive and, if needed, hits aimharder.com/hidereload
        to refresh auth cookies.
        """
        try:
            response = self.session.get(
                f"{self.base_url}/cookiealive",
                headers={
                    "Accept": "application/json, text/plain, */*",
                    "Referer": f"{self.base_url}/schedule?cl",
                    "User-Agent": self.BROWSER_USER_AGENT,
                },
            )
            response.raise_for_status()
            payload = self._get_response_payload(response)
            if isinstance(payload, dict) and payload.get("cookieAlive"):
                return
            logger.info("Cookie not alive according to /cookiealive. Refreshing session cookies")
        except Exception as e:
            logger.warning("Cookie alive check failed: %s", e)

        try:
            self.session.get(
                "https://aimharder.com/hidereload?close=1",
                headers={
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Referer": f"{self.base_url}/schedule?cl",
                    "User-Agent": self.BROWSER_USER_AGENT,
                },
            )
            self._init_box_session()
        except Exception as e:
            logger.warning("hidereload cookie refresh failed: %s", e)

    def _api_headers(self):
        return {
            "Accept": "*/*",
            "X-Requested-With": "XMLHttpRequest",
            "Origin": self.base_url,
            "Referer": f"{self.base_url}/schedule?cl",
            "User-Agent": self.BROWSER_USER_AGENT,
        }

    @staticmethod
    def _login(
        email: str,
        password: str,
        proxy: Optional[str] = None,
        box_name: Optional[str] = None,
    ) -> Session:
        session = Session()
        session.proxies = {"https": proxy}
        session.headers.update(
            {
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "en-US,en;q=0.9",
                "User-Agent": AimHarderClient.BROWSER_USER_AGENT,
            }
        )
        endpoint = login_endpoint(box_name) if box_name else LOGIN_ENDPOINT
        logger.info(f"Using proxy: {'yes' if proxy else 'no'}")
        response = session.post(
            endpoint,
            data={
                "login": "Log in",
                "mail": email,
                "pw": password,
            },
        )
        response.raise_for_status()
        soup = BeautifulSoup(response.content, "html.parser").find(id=ERROR_TAG_ID)
        if soup is not None:
            if TooManyWrongAttempts.key_phrase in soup.text:
                raise TooManyWrongAttempts
            elif IncorrectCredentials.key_phrase in soup.text:
                raise IncorrectCredentials
        logger.info("Logged successfully")
        return session

    def get_classes(self, target_day: datetime, family_id: str | None = None):
        normalized_family_id = "" if family_id is None else family_id
        response = self.session.get(
            classes_endpoint(self.box_name),
            params={
                "box": self.box_id,
                "day": target_day.strftime("%Y%m%d"),
                "familyId": normalized_family_id,
            },
            headers=self._api_headers(),
        )
        return response.json().get("bookings")

    def _get_class_booking_state(
        self, target_day: datetime, class_id: str, family_id: str | None = None
    ) -> int | None:
        classes = self.get_classes(target_day, family_id) or []
        booked_class = next(
            (
                scheduled_class
                for scheduled_class in classes
                if str(scheduled_class.get("id")) == str(class_id)
            ),
            None,
        )
        if booked_class is None:
            return None
        return booked_class.get("bookState")

    @staticmethod
    def _get_response_payload(response):
        try:
            return response.json()
        except (ValueError, JSONDecodeError):
            return response.text

    def book_class(
        self, target_day: datetime, class_id: str, family_id: str | None = None
    ) -> bool:
        normalized_family_id = "" if family_id is None else family_id
        for request_attempt in range(self.BOOKING_REQUEST_RETRIES):
            self._ensure_cookie_alive()
            response = self.session.post(
                book_endpoint(self.box_name),
                data={
                    "id": class_id,
                    "day": target_day.strftime("%Y%m%d"),
                    "insist": 0,
                    "familyId": normalized_family_id,
                },
                headers={
                    **self._api_headers(),
                    "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                },
            )
            response_payload = self._get_response_payload(response)

            if response.status_code == HTTPStatus.OK:
                if isinstance(response_payload, dict):
                    if response_payload.get("logout") == 1:
                        if request_attempt < self.BOOKING_REQUEST_RETRIES - 1:
                            logger.warning(
                                "Booking request forced logout. Re-authenticating and retrying once."
                            )
                            self.session = self._login(
                                self.email, self.password, self.proxy, self.box_name
                            )
                            self._init_box_session()
                            continue
                        raise BookingFailed(
                            f"{MESSAGE_BOOKING_FAILED_UNKNOWN}. Session logged out "
                            f"during booking (response={response_payload})"
                        )

                    if response_payload.get("bookState") == 1:
                        return

                    if "bookState" in response_payload and response_payload["bookState"] == -2:
                        raise BookingFailed(MESSAGE_BOOKING_FAILED_NO_CREDIT)
                    if "bookState" in response_payload and response_payload["bookState"] == -12:
                        raise BookingFailed(MESSAGE_TOO_SOON_TO_BOOK)
                    if (
                        "errorMssg" not in response_payload
                        and "errorMssgLang" not in response_payload
                    ):
                        booking_state = None
                        for attempt in range(self.BOOKING_CONFIRMATION_RETRIES):
                            booking_state = self._get_class_booking_state(
                                target_day, class_id, family_id
                            )
                            if booking_state == 1:
                                return
                            if attempt < self.BOOKING_CONFIRMATION_RETRIES - 1:
                                time.sleep(self.BOOKING_CONFIRMATION_DELAY_SECONDS)
                        logger.warning(
                            "Booking request was accepted but not confirmed. "
                            "api_response=%s confirmed_book_state=%s class_id=%s day=%s",
                            response_payload,
                            booking_state,
                            class_id,
                            target_day.strftime("%Y%m%d"),
                        )
                        raise BookingFailed(
                            f"{MESSAGE_BOOKING_FAILED_UNKNOWN}. Booking not confirmed "
                            f"(bookState={booking_state}, response={response_payload})"
                        )
                logger.warning(
                    "Booking endpoint returned an unexpected payload. status_code=%s payload=%s",
                    response.status_code,
                    response_payload,
                )
                raise BookingFailed(
                    f"{MESSAGE_BOOKING_FAILED_UNKNOWN}. Unexpected booking response "
                    f"({response_payload})"
                )

            logger.warning(
                "Booking endpoint returned a non-OK status. status_code=%s payload=%s",
                response.status_code,
                response_payload,
            )
            raise BookingFailed(
                f"{MESSAGE_BOOKING_FAILED_UNKNOWN}. HTTP {response.status_code} "
                f"({response_payload})"
            )

        raise BookingFailed(MESSAGE_BOOKING_FAILED_UNKNOWN)
