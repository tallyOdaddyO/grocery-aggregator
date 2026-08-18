"""Import every model so Base.metadata is complete for Alembic autogenerate."""
from app.models.match import ProductMatch
from app.models.price import Price, PriceObservation
from app.models.product import Product, ProductVariant
from app.models.retailer import Retailer, Store
from app.models.shopping_list import ShoppingList, ShoppingListItem

__all__ = [
    "ProductMatch", "Price", "PriceObservation", "Product", "ProductVariant",
    "Retailer", "Store", "ShoppingList", "ShoppingListItem",
]
