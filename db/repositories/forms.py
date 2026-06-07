import logging
from typing import Dict, Any, Optional, List

logger = logging.getLogger('moddy.database')


class FormsRepository:
    """Tally form, submission, and answer database operations"""

    # --- Forms ---

    async def create_form(self, form_id: str, title: str, signing_secret: str) -> Dict[str, Any]:
        """Creates a new Tally form entry, returns the created row"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO forms (form_id, title, signing_secret)
                VALUES ($1, $2, $3)
                ON CONFLICT (form_id) DO UPDATE
                    SET title = EXCLUDED.title,
                        signing_secret = EXCLUDED.signing_secret
                RETURNING *
            """, form_id, title, signing_secret)
            return dict(row)

    async def get_form(self, form_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a form by its ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("SELECT * FROM forms WHERE form_id = $1", form_id)
            return dict(row) if row else None

    async def list_forms(self) -> List[Dict[str, Any]]:
        """Lists all registered forms ordered by creation date"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("SELECT * FROM forms ORDER BY created_at DESC")
            return [dict(r) for r in rows]

    async def delete_form(self, form_id: str) -> bool:
        """Deletes a form and its cascaded submissions/answers"""
        async with self.pool.acquire() as conn:
            result = await conn.execute("DELETE FROM forms WHERE form_id = $1", form_id)
            return result == "DELETE 1"

    # --- Submissions ---

    async def create_submission(self, submission_id: str, form_id: str,
                                discord_id: int) -> Dict[str, Any]:
        """Creates a new submission with status=pending, returns the created row"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow("""
                INSERT INTO submissions (submission_id, form_id, discord_id)
                VALUES ($1, $2, $3)
                RETURNING *
            """, submission_id, form_id, discord_id)
            return dict(row)

    async def get_submission(self, submission_id: str) -> Optional[Dict[str, Any]]:
        """Fetches a submission by its ID"""
        async with self.pool.acquire() as conn:
            row = await conn.fetchrow(
                "SELECT * FROM submissions WHERE submission_id = $1", submission_id
            )
            return dict(row) if row else None

    async def get_submissions_by_form(self, form_id: str,
                                      status: str = None) -> List[Dict[str, Any]]:
        """Lists submissions for a given form, optionally filtered by status"""
        async with self.pool.acquire() as conn:
            if status:
                rows = await conn.fetch("""
                    SELECT * FROM submissions
                    WHERE form_id = $1 AND status = $2
                    ORDER BY created_at DESC
                """, form_id, status)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM submissions
                    WHERE form_id = $1
                    ORDER BY created_at DESC
                """, form_id)
            return [dict(r) for r in rows]

    async def get_submissions_by_user(self, discord_id: int,
                                      form_id: str = None) -> List[Dict[str, Any]]:
        """Lists submissions for a given Discord user, optionally filtered by form"""
        async with self.pool.acquire() as conn:
            if form_id:
                rows = await conn.fetch("""
                    SELECT * FROM submissions
                    WHERE discord_id = $1 AND form_id = $2
                    ORDER BY created_at DESC
                """, discord_id, form_id)
            else:
                rows = await conn.fetch("""
                    SELECT * FROM submissions
                    WHERE discord_id = $1
                    ORDER BY created_at DESC
                """, discord_id)
            return [dict(r) for r in rows]

    async def update_submission_status(self, submission_id: str,
                                       status: str, note: str = None) -> bool:
        """Updates the status (and optionally the note) of a submission"""
        async with self.pool.acquire() as conn:
            result = await conn.execute("""
                UPDATE submissions
                SET status = $2, note = COALESCE($3, note)
                WHERE submission_id = $1
            """, submission_id, status, note)
            return result == "UPDATE 1"

    # --- Answers ---

    async def create_answers(self, submission_id: str, form_id: str,
                             answers: List[Dict[str, Any]]) -> int:
        """
        Bulk-inserts answers for a submission.
        Each answer dict must have: key, type, label, value (optional).
        Returns the number of rows inserted.
        """
        if not answers:
            return 0
        async with self.pool.acquire() as conn:
            rows = [
                (submission_id, form_id, a['key'], a['type'], a['label'], a.get('value'))
                for a in answers
            ]
            await conn.executemany("""
                INSERT INTO answers (submission_id, form_id, key, type, label, value)
                VALUES ($1, $2, $3, $4, $5, $6)
            """, rows)
            return len(rows)

    async def get_answers(self, submission_id: str) -> List[Dict[str, Any]]:
        """Fetches all answers for a given submission"""
        async with self.pool.acquire() as conn:
            rows = await conn.fetch("""
                SELECT * FROM answers
                WHERE submission_id = $1
                ORDER BY id ASC
            """, submission_id)
            return [dict(r) for r in rows]
