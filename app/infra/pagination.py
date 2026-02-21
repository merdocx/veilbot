"""
Keyset pagination utilities for better performance with large datasets
"""
from typing import Optional, Dict, Any, List, Tuple
from dataclasses import dataclass

@dataclass
class PaginationParams:
    """Parameters for keyset pagination"""
    limit: int = 50
    cursor: Optional[str] = None  # Base64 encoded cursor
    sort_by: str = "created_at"
    sort_order: str = "DESC"
    
    def __post_init__(self):
        # Validate limit
        if self.limit <= 0 or self.limit > 200:
            self.limit = 50
        
        # Validate sort order
        if self.sort_order.upper() not in ["ASC", "DESC"]:
            self.sort_order = "DESC"

class KeysetPagination:
    """Keyset pagination implementation"""
    
    @staticmethod
    def encode_cursor(created_at: int, id: int) -> str:
        """Encode cursor from timestamp and ID"""
        import base64
        cursor_data = f"{created_at}:{id}"
        return base64.b64encode(cursor_data.encode()).decode()
    
    @staticmethod
    def decode_cursor(cursor: str) -> Optional[Tuple[int, int]]:
        """Decode cursor to timestamp and ID"""
        try:
            import base64
            cursor_data = base64.b64decode(cursor.encode()).decode()
            created_at_str, id_str = cursor_data.split(":")
            return int(created_at_str), int(id_str)
        except (ValueError, TypeError):
            return None
    
    @staticmethod
    def build_keyset_where_clause(
        sort_by: str = "created_at", 
        sort_order: str = "DESC",
        cursor: Optional[str] = None,
        table_alias: str = ""
    ) -> Tuple[str, List[Any]]:
        """
        Build WHERE clause for keyset pagination
        Returns (where_clause, params)
        """
        if not cursor:
            return "", []
        
        decoded = KeysetPagination.decode_cursor(cursor)
        if not decoded:
            return "", []
        
        created_at, id = decoded
        prefix = f"{table_alias}." if table_alias else ""
        
        if sort_order.upper() == "DESC":
            where_clause = f"({prefix}{sort_by} < ? OR ({prefix}{sort_by} = ? AND {prefix}id < ?))"
        else:
            where_clause = f"({prefix}{sort_by} > ? OR ({prefix}{sort_by} = ? AND {prefix}id > ?))"
        
        return where_clause, [created_at, created_at, id]
    
    @staticmethod
    def build_keyset_order_clause(
        sort_by: str = "created_at",
        sort_order: str = "DESC",
        table_alias: str = ""
    ) -> str:
        """Build ORDER BY clause for keyset pagination"""
        # ИСПРАВЛЕНИЕ: Сортировка только по created_at и id (для стабильности), тип ключа не используется
        prefix = f"{table_alias}." if table_alias else ""
        return f"{prefix}{sort_by} {sort_order.upper()}, {prefix}id {sort_order.upper()}"

def create_pagination_response(
    items: List[Any],
    limit: int,
    sort_by: str = "created_at",
    sort_order: str = "DESC"
) -> Dict[str, Any]:
    """
    Create pagination response with next cursor
    """
    if not items or len(items) < limit:
        # No more items
        return {
            "items": items,
            "has_next": False,
            "next_cursor": None
        }
    
    # Get cursor for next page
    last_item = items[-1]
    if hasattr(last_item, 'created_at') and hasattr(last_item, 'id'):
        next_cursor = KeysetPagination.encode_cursor(last_item.created_at, last_item.id)
    elif isinstance(last_item, (list, tuple)) and len(last_item) >= 2:
        # Assuming first element is ID, second is created_at or similar
        next_cursor = KeysetPagination.encode_cursor(last_item[1], last_item[0])
    else:
        next_cursor = None
    
    return {
        "items": items,
        "has_next": True,
        "next_cursor": next_cursor
    }
