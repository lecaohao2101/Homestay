from enum import Enum


class UserRole(str, Enum):
    ADMIN = "admin"
    HOST = "host"
    GUEST = "guest"
