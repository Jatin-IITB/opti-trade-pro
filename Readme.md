# README.md
# 🚀 Options Trading Platform v2.0

A modern, production-ready options trading platform built with FastAPI, featuring Black-Scholes calculations, gamma scalping strategies, and real-time market data integration with Upstox.

## ✨ Features

### 🔐 **Authentication & Security**
- OAuth2 integration with Upstox
- Secure token storage with keyring support
- Encrypted fallback storage
- Multi-user support
- Automatic token refresh

### 📊 **Market Data & Analytics**
- Real-time options and spot data from Upstox
- Black-Scholes pricing and Greeks calculation
- Implied volatility calculation
- Realized volatility analysis (Garman-Klass & Parkinson)
- Historical data processing

### 🎯 **Trading Strategies**
- Gamma scalping framework
- Strike selection algorithms
- Risk management tools
- Brokerage and margin calculations

### 🏗️ **Modern Architecture**
- FastAPI with async support
- Pydantic models for data validation
- Comprehensive error handling
- Structured logging
- Type hints throughout
- 90%+ test coverage

## 🚀 Quick Start

### Prerequisites
- Python 3.11 or higher
- Upstox Developer Account ([Get API Keys](https://developer.upstox.com/))
- Redis (optional, for caching)

### Installation

1. **Clone the repository**
   ```bash
   git clone https://github.com/yourusername/options-trading-platform.git
   cd options-trading-platform
   ```

2. **Create virtual environment**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies**
   ```bash
   pip install -e .
   ```

4. **Set up environment variables**
   ```bash
   cp .env.example .env
   # Edit .env with your Upstox API credentials
   ```

5. **Install pre-commit hooks (optional)**
   ```bash
   pre-commit install
   ```

### Configuration

Edit your `.env` file with your Upstox credentials:

```env
UPSTOX_API_KEY=your_api_key_here
UPSTOX_SECRET_KEY=your_secret_key_here
OAUTH_REDIRECT_URI=http://localhost:8000/auth/callback
SECRET_KEY=your-secure-secret-key
```

### Running the Application

```bash
# Start the FastAPI server
uvicorn src.options_trading.main:app --reload --host 0.0.0.0 --port 8000
```

The API will be available at `http://localhost:8000`
- API Documentation: `http://localhost:8000/docs`
- Alternative Docs: `http://localhost:8000/redoc`

## 📖 API Documentation

### Authentication Endpoints

- `GET /auth/login` - Initiate OAuth2 login
- `GET /auth/callback` - Handle OAuth2 callback
- `GET /auth/status` - Check authentication status
- `POST /auth/refresh` - Refresh access token
- `POST /auth/logout` - Logout user

### Market Data Endpoints

- `GET /market/instruments` - Get instrument information
- `GET /market/expiries` - Get available expiry dates
- `GET /market/contracts` - Get option contracts
- `GET /market/candles` - Get historical OHLCV data

## 🧪 Testing

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=src/options_trading --cov-report=html

# Run specific test categories
pytest -m "unit"          # Unit tests only
pytest -m "integration"   # Integration tests only
pytest -m "auth"          # Authentication tests only
```

## 📁 Project Structure

```
src/options_trading/
├── __init__.py
├── models/              # Pydantic models
│   ├── auth.py         # Authentication models
│   └── market.py       # Market data models
├── services/           # Business logic
│   ├── auth_service.py # Authentication service
│   └── market_service.py # Market data service
├── api/               # FastAPI routes
│   └── routes/
│       ├── auth.py    # Auth endpoints
│       └── market.py  # Market endpoints
├── config/            # Configuration
│   └── settings.py    # Pydantic settings
└── utils/             # Utilities
    ├── exceptions.py  # Custom exceptions
    └── security.py    # Security utilities
```

## 🔧 Development

### Code Quality

This project uses several tools to maintain code quality:

- **Black** - Code formatting
- **isort** - Import sorting
- **flake8** - Linting
- **mypy** - Type checking
- **pre-commit** - Git hooks

Run quality checks:
```bash
# Format code
black src/ tests/

# Sort imports
isort src/ tests/

# Lint code
flake8 src/ tests/

# Type checking
mypy src/
```

### Development Environment

```bash
# Install development dependencies
pip install -e ".[dev]"

# Set up pre-commit hooks
pre-commit install

# Run pre-commit on all files
pre-commit run --all-files
```

## 🐳 Docker Support

```bash
# Build image
docker build -t options-trading-platform .

# Run container
docker run -p 8000:8000 --env-file .env options-trading-platform

# Use docker-compose
docker-compose up -d
```

## 📊 Monitoring & Logging

The platform includes comprehensive logging and monitoring:

- **Structured logging** with JSON format
- **Prometheus metrics** endpoint at `/metrics`
- **Health check** endpoint at `/health`
- **Request tracing** with correlation IDs

## 🔒 Security Features

- **Token encryption** with Fernet
- **Keyring integration** for secure storage
- **Rate limiting** on API endpoints
- **Input validation** with Pydantic
- **SQL injection protection**
- **CORS configuration**

## 📈 Performance

- **Async/await** throughout for high concurrency
- **Connection pooling** for database and Redis
- **Caching** with Redis for market data
- **Background tasks** for data processing
- **Compression** for data storage

## 🚀 Deployment

### Production Checklist

- [ ] Set `ENVIRONMENT=production` in `.env`
- [ ] Use strong `SECRET_KEY`
- [ ] Configure HTTPS redirect URI
- [ ] Set up production database
- [ ] Configure Redis cluster
- [ ] Enable logging to external service
- [ ] Set up monitoring and alerts
- [ ] Configure backup strategy

### Environment Variables

See `.env.example` for all available configuration options.

## 📚 Documentation

- [API Documentation](docs/api.md)
- [Authentication Guide](docs/auth.md)
- [Market Data Guide](docs/market-data.md)
- [Trading Strategies](docs/strategies.md)
- [Deployment Guide](docs/deployment.md)

## 🤝 Contributing

1. Fork the repository
2. Create a feature branch
3. Make your changes
4. Add tests
5. Run quality checks
6. Submit a pull request

## 📄 License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

## 🙋‍♂️ Support

- 📧 Email: your.email@example.com
- 🐛 Issues: [GitHub Issues](https://github.com/yourusername/options-trading-platform/issues)
- 💬 Discussions: [GitHub Discussions](https://github.com/yourusername/options-trading-platform/discussions)

## 🏆 Acknowledgments

- [Upstox](https://upstox.com/) for the trading API
- [FastAPI](https://fastapi.tiangolo.com/) for the web framework
- [Pydantic](https://pydantic-docs.helpmanual.io/) for data validation

---

**⚠️ Disclaimer**: This software is for educational and research purposes only. Trading in financial markets involves substantial risk. Always consult with a qualified financial advisor before making investment decisions.