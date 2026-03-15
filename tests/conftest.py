import os
import sys
import tempfile

# Корень проекта в PYTHONPATH для импорта app
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Отдельная БД для тестов (должна быть установлена до импорта app)
_db = tempfile.NamedTemporaryFile(suffix='.db', delete=False)
_db.close()
os.environ['MAOCS_DB'] = _db.name
