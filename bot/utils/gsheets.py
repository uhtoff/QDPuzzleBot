import logging

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

logger = logging.getLogger(__name__)


def get_sheet():
    creds = Credentials.from_service_account_file("google_secrets.json")
    try:
        service = build("sheets", "v4", credentials=creds)
        # Call the Sheets API
        sheet = service.spreadsheets()
        return sheet
    except HttpError:
        logger.exception("Google Sheets API error building service")


def get_drive():
    creds = Credentials.from_service_account_file("google_secrets.json")
    try:
        service = build("drive", "v3", credentials=creds)
        return service
    except HttpError:
        logger.exception("Google Drive API error building service")
