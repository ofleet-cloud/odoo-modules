{
    "name": "SaaS Fleet",
    "description": "Adds the Odoo Fleet features to Odoo.",
    "summary": "SaaS Fleet features",
    "version": "16.0.1.0.0",
    "category": "Technical",
    "author": "Log'in Line",
    "mainteners": ["Mathieu Deschamps"],
    "website": "https://www.loginline.com",
    "depends": [
        "base",
        "web",
    ],
    "external_dependencies": {},
    "data": [
        "views/ribbon.xml",    
    ],
    "assets": {
        "web.assets_backend": [
            "saas_fleet/static/src/scss/*",
        ],
    },
    "demo": [],
    "installable": True,
    "auto_install": True,
    "application": False,
    "license": "LGPL-3",
}
