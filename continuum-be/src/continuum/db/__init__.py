"""Relational storage: SQLAlchemy tables, the engine, and Alembic migrations.

Only accounts live here. Memories stay in Qdrant, accessed directly — this
package is not a step toward putting the belief graph behind an ORM.
"""
