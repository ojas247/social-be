import xml.etree.ElementTree as ET
from google.cloud import datastore

def generate_sitemap():
    # 1. Initialize the Datastore client
    # Note: Ensure GOOGLE_APPLICATION_CREDENTIALS env var is set
    client = datastore.Client()

    # 2. Query the 'published_data_v1' kind
    query = client.query(kind="Published_Data_v1")
    results = query.fetch()

    # 3. Build the XML structure
    # Standard sitemap root element
    root = ET.Element("urlset")
    root.set("xmlns", "http://www.sitemaps.org/schemas/sitemap/0.9")
    count = 1;

    for entity in results:
        # Get the "SEO" attribute value
        slug = entity.get("slugURL")
        sector = entity.get("sector")
        seo_url = f"https://marketreports.in/DataSets/{sector}/{slug}"
        print("URL #: ", count, "SEO URL: ", seo_url)
        count += 1
        
        if seo_url:
            # Create <url> tag
            url_tag = ET.SubElement(root, "url")
            # Create <loc> tag
            loc_tag = ET.SubElement(url_tag, "loc")
            
            # ElementTree automatically escapes '&' to '&amp;'
            loc_tag.text = str(seo_url)

    # 4. Convert to string and save/print
    # We use 'utf-8' to ensure proper encoding
    tree = ET.ElementTree(root)
    
    # Write to a file
    tree.write("sitemap.xml", encoding="utf-8", xml_declaration=True)
    
    # Alternatively, print to console (for debugging)
    print(ET.tostring(root, encoding='utf-8', method='xml').decode())

if __name__ == "__main__":
    generate_sitemap()