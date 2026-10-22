#!/bin/bash
# Development environment setup script

set -e

echo "🔧 Setting up DaTaK development environment..."

# Check Python version
PYTHON_VERSION=$(python3 --version 2>&1 | cut -d' ' -f2 | cut -d'.' -f1,2)
if [ "$PYTHON_VERSION" != "3.12" ]; then
    echo "⚠️  Python 3.12 recommended, found $PYTHON_VERSION"
fi

# Create virtual environment
echo "📦 Creating virtual environment..."
cd backend
python3 -m venv .venv
source .venv/bin/activate

# Install dependencies
echo "📥 Installing dependencies..."
pip install --upgrade pip
pip install -e ".[dev]"

# Install pre-commit hooks
echo "🪝 Installing pre-commit hooks..."
pre-commit install

# Create data directories
echo "📁 Creating data directories..."
mkdir -p data/exports data/buffer ../configs/dbc

# Copy example config
if [ ! -f ../configs/gateway.yaml ]; then
    cp ../configs/gateway.example.yaml ../configs/gateway.yaml
    echo "📄 Created configs/gateway.yaml from example"
fi

# Initialize database
echo "🗄️ Initializing database..."
python -c "
import asyncio
from app.db.session import init_db
from app.models.user import User
from app.core.security import hash_password
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from app.config import get_settings

async def setup():
    from app.db.session import engine, async_session_factory
    from app.models.base import Base
    
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    
    async with async_session_factory() as session:
        # Create admin user
        admin = User(
            username='admin',
            password_hash=hash_password('admin'),
            role='ADMIN',
            is_active=True,
        )
        session.add(admin)
        await session.commit()
        print('👤 Created admin user (password: admin)')

asyncio.run(setup())
"

echo ""
echo "✅ Development environment ready!"
echo ""
echo "Quick start:"
echo "  cd backend"
echo "  source .venv/bin/activate"
echo "  python -m app.main"
echo ""
echo "Or with Docker:"
echo "  docker-compose -f docker/docker-compose.yml up -d"
echo ""
echo "Default admin credentials: admin / admin"
