"""
register_all.py - registers every StockScopes blueprint in one call.

In your main app file, after `app = Flask(...)`, add just these two lines:
    from register_all import register_all
    register_all(app)
"""


def register_all(app):
    from market_wrap import market_wrap_bp
    from fii_dii import fii_dii_bp
    from results_calendar import results_bp
    from stock_spotlight import spotlight_bp
    from auto_posts import auto_bp

    for bp in (market_wrap_bp, fii_dii_bp, results_bp, spotlight_bp, auto_bp):
        if bp.name not in app.blueprints:
            app.register_blueprint(bp)

    # The scanner is separate: if it ever has a problem, the rest of the API keeps working.
    try:
        from scanner import scanner_bp
        if scanner_bp.name not in app.blueprints:
            app.register_blueprint(scanner_bp)
    except Exception as e:
        print("scanner not loaded:", e)
