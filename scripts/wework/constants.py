MEMBER_BASE = "https://members.wework.com"
AUTH0_CLIENT = (
    "eyJuYW1lIjoiQGF1dGgwL2F1dGgwLWFuZ3VsYXIiLCJ2ZXJzaW9uIjoiMS4xMS4x"
    "LmN1c3RvbSIsImVudiI6eyJhbmd1bGFyL2NvcmUiOiIxMy4xLjEifX0="
)
KEYCHAIN_SERVICE = "wework-book-a-desk"
DEFAULT_KEYCHAIN_ACCOUNT = "default"
TOKEN_SUFFIX = "token"
USERNAME_SUFFIX = "username"
PASSWORD_SUFFIX = "password"
UPCOMING_BOOKINGS_URL = (
    MEMBER_BASE
    + "/workplaceone/api/common-booking/get-app-upcoming-bookings"
    + "?isPastBooking=false&platFormType=1&startDate=&endDate="
)
CANCEL_BOOKING_URL = MEMBER_BASE + "/workplaceone/api/common-booking/cancel?isOnDemand=false&platFormType=1"
