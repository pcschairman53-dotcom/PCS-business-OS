"""
PCS Business OS - Role-Based Access Control & Hierarchies
File: /app/core/rbac.py
"""

from enum import Enum
from typing import Dict, List, Set
from fastapi import HTTPException, status

from app.models.user import UserResponse


class SystemRole(str, Enum):
    """Available system security roles."""
    ADMIN = "admin"
    MANAGER = "manager"
    USER = "user"
    GUEST = "guest"


# Define the hierarchy level (higher value = higher authority)
ROLE_HIERARCHY_LEVELS: Dict[SystemRole, int] = {
    SystemRole.ADMIN: 100,
    SystemRole.MANAGER: 50,
    SystemRole.USER: 10,
    SystemRole.GUEST: 1,
}


# Define static granular permissions mapped to specific modules
ROLE_PERMISSIONS: Dict[SystemRole, Set[str]] = {
    SystemRole.GUEST: {
        "dashboard:read",
    },
    SystemRole.USER: {
        "dashboard:read",
        "documents:read",
        "documents:create",
        "tasks:read",
        "tasks:write",
        "rag:query",
    },
    SystemRole.MANAGER: {
        "dashboard:read",
        "documents:read",
        "documents:create",
        "documents:delete",
        "tasks:read",
        "tasks:write",
        "tasks:delete",
        "rag:query",
        "rag:train",
        "reports:read",
    },
    SystemRole.ADMIN: {
        "dashboard:read",
        "documents:read",
        "documents:create",
        "documents:delete",
        "tasks:read",
        "tasks:write",
        "tasks:delete",
        "rag:query",
        "rag:train",
        "rag:admin",
        "reports:read",
        "reports:write",
        "users:read",
        "users:write",
        "billing:read",
        "billing:write",
    }
}


class RBACManager:
    """
    Enterprise-grade authorization class supporting both Role Hierarchies
    and Granular Permission matrix mapping.
    """

    @staticmethod
    def has_role_minimum_level(user_role: str, required_role: SystemRole) -> bool:
        """
        Validates if a user's role is hierarchically superior or equal to the required role.
        For example: a SystemRole.ADMIN automatically satisfies a requirement for SystemRole.MANAGER.
        """
        try:
            user_enum = SystemRole(user_role)
        except ValueError:
            return False

        user_level = ROLE_HIERARCHY_LEVELS.get(user_enum, 0)
        required_level = ROLE_HIERARCHY_LEVELS.get(required_role, 999)

        return user_level >= required_level

    @staticmethod
    def has_permission(user_role: str, required_permission: str) -> bool:
        """
        Validates if a role is explicitly granted a specific micro-permission.
        """
        try:
            user_enum = SystemRole(user_role)
        except ValueError:
            return False

        granted_permissions = ROLE_PERMISSIONS.get(user_enum, set())
        return required_permission in granted_permissions


class PermissionChecker:
    """
    FastAPI dependency factory enforcing granular permission requirements.
    Can be loaded directly as a Route dependency.
    """
    def __init__(self, required_permission: str):
        self.required_permission = required_permission

    def __call__(self, current_user: UserResponse) -> UserResponse:
        # Superusers bypass all static permission checks
        if current_user.is_superuser:
            return current_user

        if not RBACManager.has_permission(current_user.role, self.required_permission):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: missing required permission: '{self.required_permission}'."
            )
        return current_user


class HierarchicalRoleChecker:
    """
    FastAPI dependency factory enforcing hierarchical role access.
    Admins are automatically allowed access if the route requires a Manager or User role.
    """
    def __init__(self, minimum_role: SystemRole):
        self.minimum_role = minimum_role

    def __call__(self, current_user: UserResponse) -> UserResponse:
        if current_user.is_superuser:
            return current_user

        if not RBACManager.has_role_minimum_level(current_user.role, self.minimum_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Access denied: current role '{current_user.role}' does not meet minimum level requirement '{self.minimum_role.value}'."
            )
        return current_user
