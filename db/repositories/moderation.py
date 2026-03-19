import json
import logging
import secrets
from datetime import datetime, timezone
from typing import Dict, Any, Optional, List

logger = logging.getLogger('moddy.database')


class ModerationRepository:
    """Moderation case management database operations"""

    async def create_moderation_case(
        self,
        case_type: str,
        sanction_type: str,
        entity_type: str,
        entity_id: int,
        reason: str,
        created_by: int,
        evidence: Optional[str] = None,
        duration: Optional[int] = None
    ) -> str:
        """
        Create a new moderation case

        Args:
            case_type: Type of case (interserver/global)
            sanction_type: Type of sanction
            entity_type: Type of entity (user/guild)
            entity_id: ID of the entity
            reason: Reason for the sanction
            created_by: Staff member who created the case
            evidence: Evidence/proof (optional)
            duration: Duration in seconds for timeout (optional)

        Returns:
            case_id: ID of the created case (hex format)
        """
        async with self.pool.acquire() as conn:
            # Generate a unique hex ID
            while True:
                case_id = secrets.token_hex(4).upper()  # 8 characters

                # Check if ID already exists
                exists = await conn.fetchval(
                    "SELECT EXISTS(SELECT 1 FROM moderation_cases WHERE case_id = $1)",
                    case_id
                )

                if not exists:
                    break

            await conn.execute("""
                INSERT INTO moderation_cases (
                    case_id, case_type, sanction_type, entity_type, entity_id,
                    reason, evidence, duration, created_by, created_at, updated_at
                )
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, NOW(), NOW())
            """,
                case_id, case_type, sanction_type, entity_type, entity_id,
                reason, evidence, duration, created_by
            )
            return case_id

    async def get_moderation_case(self, case_id: str) -> Optional[Dict[str, Any]]:
        """Get a moderation case by ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM moderation_cases WHERE case_id = $1",
                case_id
            )
            if not row:
                return None

            case_dict = dict(row)
            # Parse staff_notes JSONB
            case_dict['staff_notes'] = self._parse_jsonb(row['staff_notes'])
            return case_dict

    async def get_entity_cases(
        self,
        entity_type: str,
        entity_id: int,
        status: Optional[str] = None,
        case_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all cases for an entity

        Args:
            entity_type: Type of entity (user/guild)
            entity_id: ID of the entity
            status: Filter by status (open/closed) - optional
            case_type: Filter by case type (interserver/global) - optional
        """
        async with self.pool.acquire() as conn:
            query = """
                SELECT * FROM moderation_cases
                WHERE entity_type = $1 AND entity_id = $2
            """
            params = [entity_type, entity_id]

            if status:
                query += f" AND status = ${len(params) + 1}"
                params.append(status)

            if case_type:
                query += f" AND case_type = ${len(params) + 1}"
                params.append(case_type)

            query += " ORDER BY created_at DESC"

            rows = await conn.fetch(query, *params)

            result = []
            for row in rows:
                case_dict = dict(row)
                case_dict['staff_notes'] = self._parse_jsonb(row['staff_notes'])
                result.append(case_dict)

            return result

    async def get_active_cases(
        self,
        entity_type: str,
        entity_id: int,
        case_type: Optional[str] = None,
        sanction_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get active (open) cases for an entity

        Args:
            entity_type: Type of entity (user/guild)
            entity_id: ID of the entity
            case_type: Filter by case type - optional
            sanction_type: Filter by sanction type - optional
        """
        async with self.pool.acquire() as conn:
            query = """
                SELECT * FROM moderation_cases
                WHERE entity_type = $1 AND entity_id = $2 AND status = 'open'
            """
            params = [entity_type, entity_id]

            if case_type:
                query += f" AND case_type = ${len(params) + 1}"
                params.append(case_type)

            if sanction_type:
                query += f" AND sanction_type = ${len(params) + 1}"
                params.append(sanction_type)

            query += " ORDER BY created_at DESC"

            rows = await conn.fetch(query, *params)

            result = []
            for row in rows:
                case_dict = dict(row)
                case_dict['staff_notes'] = self._parse_jsonb(row['staff_notes'])
                result.append(case_dict)

            return result

    async def has_active_sanction(
        self,
        entity_type: str,
        entity_id: int,
        sanction_type: str
    ) -> bool:
        """Check if entity has an active sanction of a specific type"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                SELECT EXISTS(
                    SELECT 1 FROM moderation_cases
                    WHERE entity_type = $1 AND entity_id = $2
                    AND sanction_type = $3 AND status = 'open'
                )
            """, entity_type, entity_id, sanction_type)

            return row['exists']

    async def update_moderation_case(
        self,
        case_id: str,
        updated_by: int,
        reason: Optional[str] = None,
        evidence: Optional[str] = None,
        duration: Optional[int] = None,
        sanction_type: Optional[str] = None
    ) -> bool:
        """
        Update a moderation case

        Returns:
            True if case was updated, False if not found
        """
        async with self.pool.acquire() as conn:
            # Build dynamic update query
            updates = ["updated_by = $1", "updated_at = NOW()"]
            params = [updated_by]
            param_num = 2

            if reason is not None:
                updates.append(f"reason = ${param_num}")
                params.append(reason)
                param_num += 1

            if evidence is not None:
                updates.append(f"evidence = ${param_num}")
                params.append(evidence)
                param_num += 1

            if duration is not None:
                updates.append(f"duration = ${param_num}")
                params.append(duration)
                param_num += 1

            if sanction_type is not None:
                updates.append(f"sanction_type = ${param_num}")
                params.append(sanction_type)
                param_num += 1

            params.append(case_id)

            query = f"""
                UPDATE moderation_cases
                SET {', '.join(updates)}
                WHERE case_id = ${param_num}
            """

            result = await conn.execute(query, *params)
            return result == "UPDATE 1"

    async def close_moderation_case(
        self,
        case_id: str,
        closed_by: int,
        close_reason: Optional[str] = None
    ) -> bool:
        """
        Close a moderation case

        Returns:
            True if case was closed, False if not found
        """
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE moderation_cases
                SET status = 'closed',
                    closed_by = $1,
                    closed_at = NOW(),
                    close_reason = $2,
                    updated_at = NOW()
                WHERE case_id = $3 AND status = 'open'
            """, closed_by, close_reason, case_id)

            return result == "UPDATE 1"

    async def add_case_note(
        self,
        case_id: str,
        staff_id: int,
        note: str
    ) -> bool:
        """
        Add a staff note to a case

        Returns:
            True if note was added, False if case not found
        """
        async with self.pool.acquire() as conn:
            # Get current notes
            row = await conn.fetchrow(
                "SELECT staff_notes FROM moderation_cases WHERE case_id = $1",
                case_id
            )

            if not row:
                return False

            notes = self._parse_jsonb(row['staff_notes'])

            # Add new note
            notes.append({
                'staff_id': staff_id,
                'note': note,
                'timestamp': datetime.now(timezone.utc).isoformat()
            })

            # Update
            result = await conn.execute("""
                UPDATE moderation_cases
                SET staff_notes = $1::jsonb,
                    updated_at = NOW()
                WHERE case_id = $2
            """, json.dumps(notes), case_id)

            return result == "UPDATE 1"

    async def get_all_cases(
        self,
        limit: int = 50,
        offset: int = 0,
        status: Optional[str] = None,
        case_type: Optional[str] = None
    ) -> List[Dict[str, Any]]:
        """
        Get all moderation cases (for staff)

        Args:
            limit: Maximum number of cases to return
            offset: Offset for pagination
            status: Filter by status - optional
            case_type: Filter by case type - optional
        """
        async with self.pool.acquire() as conn:
            query = "SELECT * FROM moderation_cases WHERE 1=1"
            params = []
            param_num = 1

            if status:
                query += f" AND status = ${param_num}"
                params.append(status)
                param_num += 1

            if case_type:
                query += f" AND case_type = ${param_num}"
                params.append(case_type)
                param_num += 1

            query += f" ORDER BY created_at DESC LIMIT ${param_num} OFFSET ${param_num + 1}"
            params.extend([limit, offset])

            rows = await conn.fetch(query, *params)

            result = []
            for row in rows:
                case_dict = dict(row)
                case_dict['staff_notes'] = self._parse_jsonb(row['staff_notes'])
                result.append(case_dict)

            return result
