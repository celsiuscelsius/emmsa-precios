from playwright.sync_api import sync_playwright
from datetime import date
from supabase import create_client
import json
import os
from dotenv import load_dotenv

# 1. Cargar la "caja fuerte" (el archivo .env)
load_dotenv()

# 2. Leer las variables desde el entorno
SUPABASE_URL = os.getenv("SUPABASE_URL")
SUPABASE_SERVICE_KEY = os.getenv("SUPABASE_SERVICE_KEY")

# 3. Comprobación de seguridad
if not SUPABASE_URL or not SUPABASE_SERVICE_KEY:
    print("❌ Error: No se encontraron las claves de Supabase. Asegúrate de tener tu archivo .env configurado correctamente.")
    exit(1)

def scrape_emmsa():
    hoy = date.today().strftime("%d/%m/%Y")
    print(f"Scrapeando precios del {hoy}...")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()

        page.goto(
            "https://old.emmsa.com.pe/emmsa_spv/rpEstadistica/rpt_precios-diarios-web.php",
            timeout=60000,
            wait_until="domcontentloaded"
        )

        page.wait_for_selector("input[name='chkChanging']", timeout=20000)
        page.check("input[name='chkChanging']")
        print("✓ Checkbox 'Todos' marcado")

        page.wait_for_timeout(1500)
        page.click("button:has-text('Consultar')")
        print("✓ Consultando, esperando resultados...")

        page.wait_for_load_state("networkidle", timeout=30000)
        page.wait_for_timeout(2000)

        filas = page.query_selector_all("table tr")
        print(f"Filas encontradas: {len(filas)}")

        productos = []
        for fila in filas[1:]:
            celdas = fila.query_selector_all("td")
            if len(celdas) >= 4:
                try:
                    nombre   = celdas[0].inner_text().strip()
                    variedad = celdas[1].inner_text().strip()
                    precio_min = float(celdas[2].inner_text().strip().replace(",", "."))
                    precio_max = float(celdas[3].inner_text().strip().replace(",", "."))
                    precio_avg = round((precio_min + precio_max) / 2, 2)

                    if nombre:
                        productos.append({
                            "nombre":   nombre,
                            "variedad": variedad,
                            "min":      precio_min,
                            "max":      precio_max,
                            "avg":      precio_avg
                        })
                except:
                    pass

        browser.close()

    if not productos:
        print("⚠ No se encontraron productos.")
        return

    resultado = {"fecha": hoy, "productos": productos}

    # Guarda localmente
    with open("precios.json", "w", encoding="utf-8") as f:
        json.dump(resultado, f, ensure_ascii=False, indent=2)
    print(f"✓ {len(productos)} productos guardados localmente")

    # Sube a Supabase Storage con las credenciales seguras
    print("Subiendo a Supabase...")
    supabase = create_client(SUPABASE_URL, SUPABASE_SERVICE_KEY)

    with open("precios.json", "rb") as f:
        supabase.storage.from_("precios").upload(
            path="precios.json",
            file=f,
            file_options={"content-type": "application/json", "upsert": "true"}
        )

    print("✓ Subido a Supabase correctamente")
    print(f"\nURL pública de tus datos:")
    print(f"{SUPABASE_URL}/storage/v1/object/public/precios/precios.json")

    # Vista previa
    print("\nVista previa (primeros 5):")
    for prod in productos[:5]:
        print(f"  {prod['nombre']} ({prod['variedad']}) → min: {prod['min']} / max: {prod['max']} / prom: {prod['avg']}")

if __name__ == "__main__":
    scrape_emmsa()
