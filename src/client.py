import time
from datetime import datetime
from http import HTTPStatus
from typing import Optional

from bs4 import BeautifulSoup
from requests import Session
from requests.exceptions import JSONDecodeError

from constants import (
    LOGIN_ENDPOINT,
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

    def __init__(
        self,
        email: str,
        password: str,
        box_id: int,
        box_name: str,
        proxy: Optional[str] = None,
    ):
        self.session = self._login(email, password, proxy)
        self.box_id = box_id
        self.box_name = box_name

    @staticmethod
    def _login(email: str, password: str, proxy: Optional[str] = None) -> Session:
        session = Session()
        session.proxies = {"https": proxy}
        logger.info(f"Using proxy: {'yes' if proxy else 'no'}")
        response = session.post(
            LOGIN_ENDPOINT,
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
        response = self.session.get(
            classes_endpoint(self.box_name),
            params={
                "box": self.box_id,
                "day": target_day.strftime("%Y%m%d"),
                "familyId": family_id,
            },
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
        response = self.session.post(
            book_endpoint(self.box_name),
            data={
                "id": class_id,
                "day": target_day.strftime("%Y%m%d"),
                "insist": 0,
                "familyId": family_id,
            },
        )
        response_payload = self._get_response_payload(response)
        if response.status_code == HTTPStatus.OK:
            if isinstance(response_payload, dict):
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
