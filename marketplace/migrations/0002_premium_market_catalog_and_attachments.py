from django.db import migrations, models
import django.db.models.deletion


CATALOG = {
    "SHOW-001": {
        "title": "Ceramic Dinner Plate Set",
        "description": "A clean, versatile ceramic dinnerware set for everyday meals, hospitality and home entertaining. Easy to pair with existing tableware and ideal for households, restaurants and small hospitality businesses.",
        "image_url": "https://images.unsplash.com/photo-1530834812229-7c2189840e5f?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Tracey Hocking",
        "tags": "homeware,ceramic,plates,dinnerware,kitchen",
        "highlights": ["Durable everyday tableware", "Clean neutral finish", "Live KOFAD stock"],
        "featured": True, "sort_order": 10,
    },
    "SHOW-003": {
        "title": "Non-stick Frying Pan · 28cm",
        "description": "A practical 28cm non-stick frying pan sized for everyday family cooking. The broad cooking surface handles breakfast, sautéing, shallow frying and quick one-pan meals with easier food release and cleanup.",
        "image_url": "https://images.unsplash.com/photo-1581622558638-818128465982?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Nathan Dumlao",
        "tags": "homeware,kitchen,cookware,frying pan,non-stick",
        "highlights": ["28cm everyday size", "Easy-release cooking surface", "Delivery or pickup"],
        "featured": True, "sort_order": 20,
    },
    "SHOW-006": {
        "title": "Electric Kettle · 2L",
        "description": "A roomy 2-litre electric kettle for fast everyday boiling at home, in the office or in a small hospitality setting. A straightforward countertop essential for tea, coffee and hot-water preparation.",
        "image_url": "https://images.unsplash.com/photo-1603251871185-8f18ed9bc9f2?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · ORIENTO",
        "tags": "homeware,kettle,electric appliance,kitchen,2l",
        "highlights": ["2-litre capacity", "Everyday countertop appliance", "Secure online checkout"],
        "featured": True, "sort_order": 30,
    },
    "SHOW-013": {
        "title": "Body Lotion · 400ml",
        "description": "A convenient 400ml body-lotion format for daily skin care. Sized for regular household use and suitable for customers looking for an easy everyday moisturising essential.",
        "image_url": "https://images.unsplash.com/photo-1704305861425-8683b791e638?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Shashank Singh",
        "tags": "personal care,body lotion,skincare,moisturiser,400ml",
        "highlights": ["400ml everyday size", "Personal-care essential", "Tracked order fulfilment"],
        "featured": False, "sort_order": 40,
    },
    "SHOW-019": {
        "title": "A4 Exercise Book · 80 Leaves",
        "description": "An 80-leaf A4 exercise book for school, training, office notes and everyday record keeping. A dependable stationery staple for students, teams and business use.",
        "image_url": "https://images.unsplash.com/photo-1743385779312-73ea241025d8?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Kelly Sikkema",
        "tags": "stationery,exercise book,a4,notebook,school,office",
        "highlights": ["A4 writing format", "80 leaves", "Suitable for school and office"],
        "featured": False, "sort_order": 50,
    },
    "SHOW-027": {
        "title": "Screwdriver Set · 6 Piece",
        "description": "A compact six-piece screwdriver set for common household, workshop and maintenance tasks. Useful for routine fastening, fitting and repair work where multiple driver sizes are needed.",
        "image_url": "https://images.unsplash.com/photo-1770656505713-b0fd2f5751e6?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · PB Swiss Tools",
        "tags": "hardware,tools,screwdriver,repair,workshop",
        "highlights": ["6-piece utility set", "Home and workshop use", "Live availability"],
        "featured": True, "sort_order": 60,
    },
    "SHOW-030": {
        "title": "Reinforced Work Gloves",
        "description": "Protective work gloves designed for practical handling tasks in workshops, stores, loading areas and general maintenance environments. A useful everyday PPE item for grip and hand protection.",
        "image_url": "https://images.unsplash.com/photo-1664263655866-403f2025677d?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Jimmy Nilsson Masth",
        "tags": "hardware,ppe,work gloves,safety,workshop",
        "highlights": ["Reinforced workwear", "Handling and workshop use", "Business-ready PPE"],
        "featured": False, "sort_order": 70,
    },
    "SHOW-033": {
        "title": "Large School Backpack",
        "description": "A roomy everyday backpack for books, personal items and school essentials. Built around practical carrying space and a straightforward design that works for students and general daily use.",
        "image_url": "https://images.unsplash.com/photo-1681334921873-a6ba359a3388?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Erik Mclean",
        "tags": "travel,backpack,school bag,student,bag",
        "highlights": ["Large carrying capacity", "School and daily use", "Pickup or delivery"],
        "featured": True, "sort_order": 80,
    },
    "SHOW-046": {
        "title": "Canvas Sneakers",
        "description": "Versatile canvas sneakers for casual everyday wear. A clean, easy-to-style footwear option suited to school, errands, travel and relaxed daily use.",
        "image_url": "https://images.unsplash.com/photo-1669671943625-e20799ee5f42?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Maria Fernanda Pissioli",
        "tags": "footwear,sneakers,canvas shoes,casual,fashion",
        "highlights": ["Everyday casual footwear", "Easy-to-style silhouette", "Tracked ordering"],
        "featured": True, "sort_order": 90,
    },
    "SHOW-049": {
        "title": "Power Bank · 10,000mAh",
        "description": "A 10,000mAh portable power bank for keeping phones and small USB-powered devices charged away from a wall outlet. A practical travel, office and emergency-power accessory.",
        "image_url": "https://images.unsplash.com/photo-1525858907241-d230b66fb9fa?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Zoltan Tasi",
        "tags": "electronics,power bank,10000mah,charger,mobile",
        "highlights": ["10,000mAh capacity", "Portable charging", "Secure Paystack checkout"],
        "featured": True, "sort_order": 100,
    },
    "SHOW-050": {
        "title": "Bluetooth Mini Speaker",
        "description": "A compact wireless Bluetooth speaker for music, spoken audio and casual everyday listening. Easy to carry between rooms, workspaces and social settings.",
        "image_url": "https://images.unsplash.com/photo-1633806442577-201eb855c2d3?auto=format&fit=crop&w=1200&q=82",
        "image_credit": "Unsplash · Alexander Grey",
        "tags": "electronics,bluetooth,speaker,wireless,audio",
        "highlights": ["Compact wireless audio", "Bluetooth connectivity", "Order tracking included"],
        "featured": True, "sort_order": 110,
    },
}


def enrich_showcase_market(apps, schema_editor):
    Product = apps.get_model("core", "Product")
    MarketListing = apps.get_model("marketplace", "MarketListing")
    for sku, data in CATALOG.items():
        product = Product.objects.filter(sku=sku, active=True).first()
        if not product:
            continue
        listing, _ = MarketListing.objects.get_or_create(product=product)
        listing.enabled = True
        listing.featured = data["featured"]
        listing.title = data["title"]
        listing.description = data["description"]
        listing.price_source = "retail_unit"
        listing.sort_order = data["sort_order"]
        if not listing.image_data:
            listing.image_url = data["image_url"]
            listing.image_credit = data["image_credit"]
        listing.tags = data["tags"]
        listing.highlights = data["highlights"]
        listing.save()


class Migration(migrations.Migration):
    dependencies = [("marketplace", "0001_initial")]

    operations = [
        migrations.AddField(
            model_name="marketlisting",
            name="highlights",
            field=models.JSONField(blank=True, default=list),
        ),
        migrations.AddField(
            model_name="marketlisting",
            name="image_credit",
            field=models.CharField(blank=True, default="", max_length=180),
        ),
        migrations.AddField(
            model_name="marketlisting",
            name="image_url",
            field=models.URLField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="marketlisting",
            name="tags",
            field=models.CharField(blank=True, default="", max_length=320),
        ),
        migrations.CreateModel(
            name="ConversationAttachment",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("original_name", models.CharField(max_length=220)),
                ("mime_type", models.CharField(max_length=100)),
                ("size", models.PositiveIntegerField(default=0)),
                ("sha256", models.CharField(max_length=64)),
                ("data", models.BinaryField(editable=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("message", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="attachments", to="marketplace.conversationmessage")),
            ],
            options={"ordering": ["pk"]},
        ),
        migrations.RunPython(enrich_showcase_market, migrations.RunPython.noop),
    ]
