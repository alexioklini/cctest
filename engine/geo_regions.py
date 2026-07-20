"""Region-preserving city substitution data for the PII pseudonymiser.

A faked place name should stay in the SAME country as the original (user
request 9.384.1: 'Köln' → another German city, not 'Burlington'). We look up
the original's country from a curated city→country map, then draw the
replacement from that country's canonical city list. Unknown cities fall back
to a neutral pool (handled in pseudonymizer, not here).

Countries: DE, AT, CH (German-speaking, most detailed), FR, IT, GB, US, NL,
BE, ES, PL, CZ. Purely static data — no runtime deps, no network.

`CITY_TO_COUNTRY` includes both German and native spellings (Rom/Roma) so
matching an original works either way. `COUNTRY_TO_CITIES` is the CANONICAL,
dedup-free replacement pool (one form per city) so a substitution never mixes
spelling styles.
"""
from __future__ import annotations

CITY_TO_COUNTRY: dict[str, str] = {
    # Deutschland (25 größte)
    'Berlin': 'DE', 'Hamburg': 'DE', 'München': 'DE', 'Köln': 'DE',
    'Frankfurt': 'DE', 'Stuttgart': 'DE', 'Düsseldorf': 'DE', 'Leipzig': 'DE',
    'Dortmund': 'DE', 'Essen': 'DE', 'Bremen': 'DE', 'Dresden': 'DE',
    'Hannover': 'DE', 'Nürnberg': 'DE', 'Duisburg': 'DE', 'Bochum': 'DE',
    'Wuppertal': 'DE', 'Bielefeld': 'DE', 'Bonn': 'DE', 'Münster': 'DE',
    'Mannheim': 'DE', 'Karlsruhe': 'DE', 'Augsburg': 'DE', 'Wiesbaden': 'DE',
    'Mönchengladbach': 'DE',
    # Österreich (ausführlich)
    'Wien': 'AT', 'Graz': 'AT', 'Linz': 'AT', 'Salzburg': 'AT',
    'Innsbruck': 'AT', 'Klagenfurt': 'AT', 'Villach': 'AT', 'Wels': 'AT',
    'St. Pölten': 'AT', 'Dornbirn': 'AT', 'Wiener Neustadt': 'AT',
    'Steyr': 'AT', 'Feldkirch': 'AT', 'Bregenz': 'AT', 'Leonding': 'AT',
    'Klosterneuburg': 'AT', 'Baden': 'AT', 'Wolfsberg': 'AT', 'Krems': 'AT',
    # Schweiz
    'Zürich': 'CH', 'Genf': 'CH', 'Genève': 'CH', 'Basel': 'CH', 'Bern': 'CH',
    'Lausanne': 'CH', 'Winterthur': 'CH', 'Luzern': 'CH', 'St. Gallen': 'CH',
    'Lugano': 'CH', 'Biel': 'CH', 'Thun': 'CH', 'Köniz': 'CH',
    'La Chaux-de-Fonds': 'CH', 'Freiburg im Üechtland': 'CH',
    'Schaffhausen': 'CH', 'Chur': 'CH', 'Neuenburg': 'CH', 'Sitten': 'CH',
    # Frankreich
    'Paris': 'FR', 'Lyon': 'FR', 'Marseille': 'FR', 'Toulouse': 'FR',
    'Nizza': 'FR', 'Nice': 'FR', 'Nantes': 'FR', 'Straßburg': 'FR',
    'Strasbourg': 'FR', 'Montpellier': 'FR', 'Bordeaux': 'FR', 'Lille': 'FR',
    'Rennes': 'FR', 'Toulon': 'FR', 'Grenoble': 'FR', 'Dijon': 'FR',
    'Le Havre': 'FR',
    # Italien (dt.+orig.)
    'Rom': 'IT', 'Roma': 'IT', 'Mailand': 'IT', 'Milano': 'IT', 'Neapel': 'IT',
    'Napoli': 'IT', 'Turin': 'IT', 'Torino': 'IT', 'Palermo': 'IT',
    'Genua': 'IT', 'Genova': 'IT', 'Bologna': 'IT', 'Florenz': 'IT',
    'Firenze': 'IT', 'Venedig': 'IT', 'Venezia': 'IT', 'Verona': 'IT',
    'Bari': 'IT',
    # Großbritannien
    'London': 'GB', 'Birmingham': 'GB', 'Manchester': 'GB', 'Glasgow': 'GB',
    'Liverpool': 'GB', 'Leeds': 'GB', 'Sheffield': 'GB', 'Edinburgh': 'GB',
    'Bristol': 'GB', 'Cardiff': 'GB', 'Belfast': 'GB', 'Nottingham': 'GB',
    'Newcastle': 'GB', 'Brighton': 'GB',
    # USA
    'New York': 'US', 'Los Angeles': 'US', 'Chicago': 'US', 'Houston': 'US',
    'Phoenix': 'US', 'Philadelphia': 'US', 'San Antonio': 'US',
    'San Diego': 'US', 'Dallas': 'US', 'San Francisco': 'US', 'Seattle': 'US',
    'Boston': 'US', 'Washington': 'US', 'Miami': 'US', 'Atlanta': 'US',
    # Niederlande
    'Amsterdam': 'NL', 'Rotterdam': 'NL', 'Den Haag': 'NL', 'Utrecht': 'NL',
    'Eindhoven': 'NL', 'Groningen': 'NL', 'Tilburg': 'NL', 'Almere': 'NL',
    'Breda': 'NL', 'Nijmegen': 'NL',
    # Belgien
    'Brüssel': 'BE', 'Antwerpen': 'BE', 'Gent': 'BE', 'Charleroi': 'BE',
    'Lüttich': 'BE', 'Brügge': 'BE', 'Namur': 'BE', 'Löwen': 'BE',
    # Spanien
    'Madrid': 'ES', 'Barcelona': 'ES', 'Valencia': 'ES', 'Sevilla': 'ES',
    'Saragossa': 'ES', 'Málaga': 'ES', 'Murcia': 'ES', 'Bilbao': 'ES',
    'Alicante': 'ES', 'Córdoba': 'ES',
    # Polen
    'Warschau': 'PL', 'Krakau': 'PL', 'Danzig': 'PL', 'Breslau': 'PL',
    'Posen': 'PL', 'Stettin': 'PL', 'Lodz': 'PL', 'Kattowitz': 'PL',
    'Lublin': 'PL',
    # Tschechien
    'Prag': 'CZ', 'Brünn': 'CZ', 'Ostrava': 'CZ', 'Pilsen': 'CZ',
    'Olmütz': 'CZ', 'Budweis': 'CZ',
}

# Canonical (dedup-free) replacement pool — one spelling per city, so a
# substitution never mixes German/native forms (Rom, not Milano-then-Roma).
COUNTRY_TO_CITIES: dict[str, tuple[str, ...]] = {
    'DE': ('Berlin', 'Hamburg', 'München', 'Köln', 'Frankfurt', 'Stuttgart',
           'Düsseldorf', 'Leipzig', 'Dortmund', 'Essen', 'Bremen', 'Dresden',
           'Hannover', 'Nürnberg', 'Duisburg', 'Bochum', 'Wuppertal',
           'Bielefeld', 'Bonn', 'Münster', 'Mannheim', 'Karlsruhe', 'Augsburg',
           'Wiesbaden', 'Mönchengladbach'),
    'AT': ('Wien', 'Graz', 'Linz', 'Salzburg', 'Innsbruck', 'Klagenfurt',
           'Villach', 'Wels', 'St. Pölten', 'Dornbirn', 'Wiener Neustadt',
           'Steyr', 'Feldkirch', 'Bregenz', 'Leonding', 'Klosterneuburg',
           'Baden', 'Wolfsberg', 'Krems'),
    'CH': ('Zürich', 'Genf', 'Basel', 'Bern', 'Lausanne', 'Winterthur',
           'Luzern', 'St. Gallen', 'Lugano', 'Biel', 'Thun', 'Köniz',
           'Schaffhausen', 'Chur', 'Neuenburg', 'Sitten'),
    'FR': ('Paris', 'Lyon', 'Marseille', 'Toulouse', 'Nizza', 'Nantes',
           'Straßburg', 'Montpellier', 'Bordeaux', 'Lille', 'Rennes', 'Toulon',
           'Grenoble', 'Dijon', 'Le Havre'),
    'IT': ('Rom', 'Mailand', 'Neapel', 'Turin', 'Palermo', 'Genua', 'Bologna',
           'Florenz', 'Venedig', 'Verona', 'Bari'),
    'GB': ('London', 'Birmingham', 'Manchester', 'Glasgow', 'Liverpool',
           'Leeds', 'Sheffield', 'Edinburgh', 'Bristol', 'Cardiff', 'Belfast',
           'Nottingham', 'Newcastle', 'Brighton'),
    'US': ('New York', 'Los Angeles', 'Chicago', 'Houston', 'Phoenix',
           'Philadelphia', 'San Antonio', 'San Diego', 'Dallas',
           'San Francisco', 'Seattle', 'Boston', 'Washington', 'Miami',
           'Atlanta'),
    'NL': ('Amsterdam', 'Rotterdam', 'Den Haag', 'Utrecht', 'Eindhoven',
           'Groningen', 'Tilburg', 'Almere', 'Breda', 'Nijmegen'),
    'BE': ('Brüssel', 'Antwerpen', 'Gent', 'Charleroi', 'Lüttich', 'Brügge',
           'Namur', 'Löwen'),
    'ES': ('Madrid', 'Barcelona', 'Valencia', 'Sevilla', 'Saragossa', 'Málaga',
           'Murcia', 'Bilbao', 'Alicante', 'Córdoba'),
    'PL': ('Warschau', 'Krakau', 'Danzig', 'Breslau', 'Posen', 'Stettin',
           'Lodz', 'Kattowitz', 'Lublin'),
    'CZ': ('Prag', 'Brünn', 'Ostrava', 'Pilsen', 'Olmütz', 'Budweis'),
}

# Lowercase index for case-insensitive lookup.
_CITY_LC = {k.lower(): v for k, v in CITY_TO_COUNTRY.items()}


def country_of_city(name: str) -> str | None:
    """ISO country code of a known city (case-insensitive), or None."""
    if not name:
        return None
    return _CITY_LC.get(name.strip().lower())
