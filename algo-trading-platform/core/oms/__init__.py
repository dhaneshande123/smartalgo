"""Order Management System -- submission, tracking, routing, slicing, bracket orders."""

from .state_machine import OrderStateMachine, InvalidTransitionError
from .audit_trail import AuditTrail, AuditEntry, AUDIT_SUBMIT, AUDIT_MODIFY, AUDIT_CANCEL, AUDIT_STATE_CHANGE, AUDIT_FILL, AUDIT_ERROR
from .order_manager import OrderManager
from .smart_router import SmartRouter, RoutingStrategy, RoutingRule, RoutingStats, NoBrokerAvailableError
from .order_slicer import OrderSlicer, SliceConfig, SliceState, ParentOrder, ChildSlice
from .bracket_order import BracketOrderManager, BracketOrder, CoverOrder, BracketState

__all__ = [
    "OrderStateMachine",
    "InvalidTransitionError",
    "AuditTrail",
    "AuditEntry",
    "AUDIT_SUBMIT",
    "AUDIT_MODIFY",
    "AUDIT_CANCEL",
    "AUDIT_STATE_CHANGE",
    "AUDIT_FILL",
    "AUDIT_ERROR",
    "OrderManager",
    "SmartRouter",
    "RoutingStrategy",
    "RoutingRule",
    "RoutingStats",
    "NoBrokerAvailableError",
    "OrderSlicer",
    "SliceConfig",
    "SliceState",
    "ParentOrder",
    "ChildSlice",
    "BracketOrderManager",
    "BracketOrder",
    "CoverOrder",
    "BracketState",
]
