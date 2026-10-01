import re
import json
import logging
import os
from bs4 import BeautifulSoup
from urllib.parse import quote, urljoin
from curl_cffi import requests as curl_requests
from src import session

base_url = "https://www.apkmirror.com"

# APKMirror gets a dedicated browser-impersonated session. Keeping one session
# across release -> variant -> download pages preserves cookies and the browser
# fingerprint throughout the complete APKMirror flow.
_apkmirror_session = curl_requests.Session(impersonate="chrome")
_blocked_by_cloudflare = False


class ApkMirrorBlocked(RuntimeError):
    """APKMirror declined this runner before it served an application page."""


def _app_slug_candidates(config: dict) -> list[str]:
    """Return the small set of valid-looking APKMirror app slugs to try.

    On APKMirror the publisher slug and app slug are sometimes identical
    (for example ``/apk/pinterest/pinterest/``), while a human-readable app
    title can be much longer.  Trying the publisher as a final fallback fixes
    those genuine 404s without a site-wide search or browser automation.
    """
    candidates = [
        config.get("app_slug"),
        config.get("name"),
        config.get("org"),
    ]
    return list(dict.fromkeys(slug for slug in candidates if slug))


def _looks_like_cloudflare_challenge(response) -> bool:
    """Detect Cloudflare challenge pages even when they return HTTP 200."""
    try:
        body = response.text[:5000].lower()
    except Exception:
        body = ""
    return (
        response.headers.get("cf-mitigated") == "challenge"
        or "just a moment" in body
        or "attention required" in body
        or "verify you are human" in body
        or "cf-chl-" in body
        or "challenges.cloudflare.com" in body
    )

def _jina_get(url: str, **kwargs):
    """Best-effort remote HTML retrieval for APKMirror challenged pages."""
    try:
        headers = dict(kwargs.pop("headers", {}) or {})
        headers.update({
            "X-Respond-With": "html",
            "X-Engine": "browser",
            "X-No-Cache": "true",
            "X-Retain-Links": "all",
        })
        api_key = os.getenv("JINA_API_KEY")
        if api_key:
            headers["Authorization"] = f"Bearer {api_key}"
            headers.setdefault("X-Proxy", "auto")
        timeout = max(int(kwargs.pop("timeout", 20)), 75)
        jina_url = f"https://r.jina.ai/{url}"
        response = session.get(jina_url, headers=headers, timeout=timeout, **kwargs)
        if response.status_code != 200 or not response.content:
            return None
        if _looks_like_cloudflare_challenge(response):
            logging.warning("Jina Reader also returned a Cloudflare challenge for %s", url)
            return None
        logging.info("APKMirror HTML obtained through Jina Reader: %s", url)
        return response
    except Exception as exc:
        logging.debug("Jina Reader fallback failed for %s: %s", url, exc)
        return None

def _cf_get(url, **kwargs):
    """Fetch APKMirror with the authenticated client profile used by APKUpdater."""
    global _blocked_by_cloudflare
    if _blocked_by_cloudflare:
        raise ApkMirrorBlocked("APKMirror blocked this runner earlier in the build")

    kwargs.setdefault("timeout", 30)
    referer = kwargs.pop("referer", None) or f"{base_url}/"
    headers = dict(kwargs.pop("headers", {}) or {})
    headers.setdefault("Referer", referer)
    headers.setdefault(
        "Accept",
        "text/html,application/xhtml+xml,application/xml;q=0.9,"
        "image/avif,image/webp,*/*;q=0.8",
    )
    headers.setdefault("Accept-Language", "en-US,en;q=0.9")
    headers.setdefault("Cache-Control", "no-cache")
    headers.setdefault("Pragma", "no-cache")
    # APKMirror exposes a client API used by APKUpdater. The API credential is
    # intentionally public in that open-source client; allow an override for
    # installations that have their own credential.
    headers.setdefault(
        "Authorization",
        os.getenv(
            "APKMIRROR_API_AUTH",
            "Basic YXBpLWFwa3VwZGF0ZXI6cm01cmNmcnVVakt5MDRzTXB5TVBKWFc4",
        ),
    )

    response = _apkmirror_session.get(url, headers=headers, **kwargs)
    if not _looks_like_cloudflare_challenge(response):
        return response

    logging.info(
        "APKMirror returned a Cloudflare challenge (HTTP %s); trying alternate HTML transports...",
        response.status_code,
    )

    jina_response = _jina_get(
        url,
        headers=headers,
        timeout=kwargs.get("timeout", 30),
    )
    if jina_response is not None:
        return jina_response

    try:
        from src import trawl
        rendered = trawl.fetch(url, referer=referer)
        if rendered:
            for name, value in rendered.cookies.items():
                _apkmirror_session.cookies.set(name, value, domain=".apkmirror.com")
            logging.info("APKMirror page obtained through the CI browser service")
            return rendered
    except Exception as exc:
        logging.debug("APKMirror browser-service fallback failed: %s", exc)

    _blocked_by_cloudflare = True
    logging.warning("APKMirror served a Cloudflare challenge and all HTML fallbacks failed.")
    raise ApkMirrorBlocked("APKMirror Cloudflare challenge")


def _direct_release_candidates(version: str, config: dict) -> list[str]:
    """Build direct APKMirror release URLs without first visiting the app page.

    APKMirror's human release slug is not always identical to the configured
    app name. Generate a few deterministic aliases from publisher/app/package
    metadata (for example facebook-messenger for package com.facebook.orca).
    """
    version_slug = version.replace(".", "-")
    org = (config.get("org") or "").strip("/")

    def _strip_numeric_suffix(value: str) -> str:
        return re.sub(r"-\d+$", "", value)

    package_leaf = (config.get("package") or "").rsplit(".", 1)[-1].strip()
    org_base = _strip_numeric_suffix(org)

    # APKMirror release slugs are not necessarily the same as the configured
    # app name. Prefer known package-specific aliases before generic names.
    # Messenger uses publisher slug "facebook-2" and release prefix
    # "facebook-messenger" rather than the configured "messenger" name.
    package_release_aliases = {
        "com.facebook.orca": ["facebook-messenger"],
        "com.facebook.katana": ["facebook"],
        "com.adobe.reader": ["adobe-acrobat-reader-edit-pdf"],
    }

    # Some fallback configs contain only package/name and therefore have no
    # APKMirror "org". Keep this correction tied to package metadata, not any
    # particular app version.
    package_path_aliases = {
        "com.facebook.orca": [
            ("facebook-2", "messenger"),
        ],
    }

    release_names = [
        *package_release_aliases.get((config.get("package") or "").strip(), []),
        config.get("release_prefix"),
        config.get("release_name"),
        config.get("name"),
        config.get("app_slug"),
    ]

    # Common APKMirror naming patterns:
    #   facebook + messenger -> facebook-messenger
    #   adobe-acrobat + reader -> adobe-acrobat-reader
    if org_base and config.get("name"):
        release_names.append(f"{org_base}-{config['name']}")
    if config.get("name") and package_leaf and package_leaf not in str(config["name"]).lower():
        release_names.append(f"{config['name']}-{package_leaf}")
    if org_base and package_leaf and package_leaf not in org_base.lower():
        release_names.append(f"{org_base}-{package_leaf}")

    app_slugs = [
        config.get("app_slug"),
        config.get("name"),
        config.get("org"),
    ]

    candidates = []
    explicit = config.get("release_url")
    if explicit:
        candidates.append(explicit)

    package_name = (config.get("package") or "").strip()

    # First try explicit package path aliases. These remain usable when runtime
    # config was synthesized from another provider and has no "org".
    for alias_org, alias_app_slug in package_path_aliases.get(package_name, []):
        for release_name in release_names:
            if not release_name:
                continue
            candidates.append(
                f"{base_url}/apk/{alias_org}/{quote(str(alias_app_slug), safe='')}/"
                f"{quote(str(release_name), safe='')}-{version_slug}-release/"
            )
            candidates.append(
                f"{base_url}/apk/{alias_org}/{quote(str(alias_app_slug), safe='')}/"
                f"{quote(str(alias_app_slug), safe='')}-{version_slug}-release/"
            )

    for app_slug in app_slugs:
        if not app_slug or not org:
            continue
        for release_name in release_names:
            if not release_name:
                continue
            candidates.append(
                f"{base_url}/apk/{org}/{quote(str(app_slug), safe='')}/"
                f"{quote(str(release_name), safe='')}-{version_slug}-release/"
            )
        # APKMirror sometimes uses the app slug itself as the release prefix.
        candidates.append(
            f"{base_url}/apk/{org}/{quote(str(app_slug), safe='')}/"
            f"{quote(str(app_slug), safe='')}-{version_slug}-release/"
        )

    return list(dict.fromkeys(candidates))


def _get_api_variant_urls(
    version: str,
    config: dict,
    target_arch: str,
) -> list[tuple[str, str | None]]:
    """Resolve APKMirror variant URLs through APKMirror's client API."""
    package = (config.get("package") or "").strip()
    if not package:
        return []

    auth = os.getenv(
        "APKMIRROR_API_AUTH",
        "Basic YXBpLWFwa3VwZGF0ZXI6cm01cmNmcnVVakt5MDRzTXB5TVBKWFc4",
    )
    headers = {
        "User-Agent": os.getenv(
            "APKMIRROR_API_USER_AGENT",
            "APKUpdater-v0",
        ),
        "Authorization": auth,
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    api_url = f"{base_url}/wp-json/apkm/v1/app_exists/"
    try:
        response = _apkmirror_session.post(
            api_url,
            headers=headers,
            json={"pnames": [package], "exclude": ["alpha", "beta"]},
            timeout=45,
        )
        if response.status_code != 200:
            logging.info("APKMirror client API returned HTTP %s", response.status_code)
            return []

        payload = response.json()
        data = payload.get("data") or []
        entry = next(
            (item for item in data if item.get("pname") == package),
            None,
        )
        if not entry:
            logging.info("APKMirror client API returned no data for %s", package)
            return []

        release = entry.get("release") or {}
        api_version = str(release.get("version") or "").strip()
        pinned_version = str(version or "").strip()

        # The API describes the current release. Never silently substitute a
        # different release for a pinned build.
        if pinned_version and api_version and api_version != pinned_version:
            normalized_pinned = pinned_version.replace(".", "").replace("-", "")
            normalized_api = api_version.replace(".", "").replace("-", "")
            if normalized_pinned != normalized_api:
                logging.info(
                    "APKMirror API release mismatch for %s: requested=%s api=%s",
                    package,
                    pinned_version,
                    api_version,
                )
                return []

        wanted_arch = (target_arch or "universal").lower()
        wanted_dpi = str(config.get("dpi") or "nodpi").lower()

        def arch_match(item):
            arches = {str(x).lower() for x in (item.get("arches") or [])}
            if wanted_arch in ("", "universal", "noarch"):
                return True
            # APKMirror sometimes labels a multi-ABI bundle as "universal"
            # even though the bundle contains the requested ABI. Prefer an
            # explicit ABI match, but allow a universal bundle as a secondary
            # candidate. The final source-contract validation remains
            # authoritative and rejects an artifact that does not satisfy the
            # patch source's package/version/signature/hash contract.
            if wanted_arch in arches:
                return True
            return any(value in {"universal", "noarch"} for value in arches)

        def dpi_match(item):
            if wanted_dpi in ("", "nodpi", "all", "120-640dpi"):
                return True

            raw_dpis = [str(x).lower() for x in (item.get("dpis") or [])]
            if not raw_dpis:
                return False

            # Config can use a range such as 240-640dpi while the API may expose
            # individual densities (e.g. 360, 480, 640) or a range per variant.
            range_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)dpi", wanted_dpi)
            if range_match:
                low, high = map(int, range_match.groups())
                for value in raw_dpis:
                    m = re.fullmatch(r"(\d+)", value)
                    if m and low <= int(m.group(1)) <= high:
                        return True
                    m = re.fullmatch(r"(\d+)\s*-\s*(\d+)dpi", value)
                    if m:
                        item_low, item_high = map(int, m.groups())
                        if item_low >= low and item_high <= high:
                            return True
                return False

            return wanted_dpi in raw_dpis

        candidates = [
            item for item in (entry.get("apks") or [])
            if str(item.get("link") or "").strip()
            and arch_match(item)
            and dpi_match(item)
        ]

        def arch_priority(item):
            arches = {str(x).lower() for x in (item.get("arches") or [])}
            if wanted_arch in ("", "universal", "noarch"):
                return 0
            if wanted_arch in arches:
                return 0
            if any(value in {"universal", "noarch"} for value in arches):
                return 1
            return 2

        # Preserve exact-ABI preference. Universal bundles are only a fallback
        # when APKMirror exposes no explicit ABI entry for the requested arch.
        candidates.sort(key=arch_priority)

        out = []
        for item in candidates:
            link = str(item.get("link") or "").strip()
            if link:
                out.append((urljoin(base_url + "/", link), api_version or pinned_version))

        logging.info(
            "APKMirror client API resolved %d candidate variant(s) for %s",
            len(out),
            package,
        )
        return out
    except Exception as exc:
        logging.warning("APKMirror client API lookup failed for %s: %s", package, exc)
        return []


def _variant_matches_extra_criteria(text: str, config: dict, version: str | None = None) -> bool:
    """Validate package-specific variant properties before accepting an APKMirror build."""
    package = str(config.get("package") or "").strip()
    if package != "com.facebook.katana":
        return True

    raw = " ".join(str(text or "").split()).lower()

    expected_code = str(config.get("expected_version_code") or "").strip()
    if expected_code and not re.search(rf"(?<!\d){re.escape(expected_code)}(?!\d)", raw):
        logging.info("Rejected Facebook variant: versionCode %s not found", expected_code)
        return False

    expected_arch = str(config.get("arch") or "arm64-v8a").lower()
    if expected_arch and expected_arch not in raw:
        logging.info("Rejected Facebook variant: architecture %s not found", expected_arch)
        return False

    android_min = int(config.get("android_min") or 11)
    android_ok = bool(re.search(rf"android\s*{android_min}\s*\+", raw))
    if not android_ok:
        android_ok = bool(re.search(rf"android\s*{android_min}\s*(?:and\s*above|or\s*later)", raw))
    if not android_ok:
        logging.info("Rejected Facebook variant: Android %s+ requirement not found", android_min)
        return False

    expected_dpi = str(config.get("dpi") or "240-640dpi").lower()
    dpi_match = re.fullmatch(r"(\d+)\s*-\s*(\d+)dpi", expected_dpi)
    if dpi_match:
        low, high = map(int, dpi_match.groups())
        if not re.search(rf"(?<!\d){low}\s*-\s*{high}\s*dpi", raw):
            logging.info("Rejected Facebook variant: DPI range %s not found", expected_dpi)
            return False

    expected_features = str(config.get("features") or "17feat").lower()
    if expected_features and not re.search(rf"(?<![a-z0-9]){re.escape(expected_features)}(?![a-z0-9])", raw):
        logging.info("Rejected Facebook variant: feature set %s not found", expected_features)
        return False

    return True


def _download_from_variant_page(
    variant_url: str,
    config: dict,
    version: str | None = None,
) -> tuple[str | None, bool]:
    """Follow APKMirror's variant -> download page -> file link chain."""
    try:
        variant_response = _cf_get(
            variant_url,
            referer=f"{base_url}/",
        )
        variant_response.raise_for_status()
        if _looks_like_cloudflare_challenge(variant_response):
            return None, False

        variant_soup = BeautifulSoup(variant_response.content, "html.parser")
        if not _variant_matches_extra_criteria(variant_soup.get_text(" ", strip=True), config, version):
            return None, True

        wanted_type = str(config.get("type") or "APK").lower()
        download_button = variant_soup.find("a", class_="downloadButton")
        if not download_button or not download_button.get("href"):
            logging.info("No downloadButton on APKMirror variant page: %s", variant_url)
            return None, True

        download_page_url = urljoin(
            base_url + "/",
            download_button["href"],
        )
        download_response = _cf_get(
            download_page_url,
            referer=variant_url,
        )
        download_response.raise_for_status()
        if _looks_like_cloudflare_challenge(download_response):
            return None, True

        download_soup = BeautifulSoup(download_response.content, "html.parser")
        direct = (
            download_soup.find("a", id="download-link")
            or download_soup.find("a", rel="nofollow")
        )
        if not direct or not direct.get("href"):
            logging.info(
                "No direct file link on APKMirror download page: %s",
                download_page_url,
            )
            return None, True

        file_url = urljoin(base_url + "/", direct["href"])
        file_path = file_url.split("?", 1)[0].lower()

        # Validate the artifact type at the final file URL, where APKMirror's
        # native extension is unambiguous. This avoids false positives from
        # unrelated "bundle" text elsewhere on the HTML page.
        if wanted_type == "bundle" and not file_path.endswith(".apkm"):
            logging.info("API variant resolved a non-APKM artifact; trying next variant")
            return None, True
        if wanted_type == "apk" and file_path.endswith(".apkm"):
            logging.info("API variant resolved an APKM artifact; trying next variant")
            return None, True

        logging.info("✓ APKMirror direct file link resolved: %s", file_url)
        return file_url, True
    except Exception as exc:
        logging.warning(
            "APKMirror variant download chain failed for %s: %s",
            variant_url,
            exc,
        )
        return None, True



def _get_direct_release_page(
    version: str,
    config: dict,
) -> tuple[BeautifulSoup | None, str | None]:
    """Try to resolve an exact release page directly from the configured slugs."""
    for url in _direct_release_candidates(version, config):
        logging.info(f"Trying direct APKMirror release URL: {url}")
        try:
            response = _cf_get(url, referer=f"{base_url}/")
        except ApkMirrorBlocked:
            return None, None

        if response.status_code != 200:
            logging.info(f"Direct release URL returned {response.status_code}: {url}")
            continue

        soup = BeautifulSoup(response.content, "html.parser")
        page_text = soup.get_text(" ", strip=True)
        normalized_version = version.replace(".", "-")
        if version in page_text or normalized_version in page_text:
            logging.info(f"✓ Direct release page found: {response.url}")
            return soup, response.url

        title = soup.find("title")
        title_text = title.get_text(" ", strip=True) if title else ""
        if version in title_text or normalized_version in title_text:
            logging.info(f"✓ Direct release page validated by title: {response.url}")
            return soup, response.url

        logging.warning(f"Direct URL returned a page without version {version}: {url}")

    return None, None

def get_build_number_for_version(version: str, config: dict) -> tuple[str | None, str]:
    """Fetch build number for a specific version from APKMirror.
    Returns (build_number, format_type) where format_type is 'parentheses' or 'build_suffix'.
    Returns the LOWEST build number found, since patches are typically made for initial builds."""
    try:
        main_url = f"{base_url}/apk/{config['org']}/{config['name']}/"
        response = _cf_get(main_url)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            # Collect all build numbers for this version
            builds_found = []
            for link in soup.find_all('a', href=True):
                text = link.get_text()
                if version in text:
                    # Format 1: "32.30.0(1575420)" -> parentheses
                    build_match = re.search(rf'{re.escape(version)}\((\d+)\)', text)
                    if build_match:
                        builds_found.append((build_match.group(1), 'parentheses'))
                    # Format 2: "6.6 build 006" -> build suffix
                    build_match = re.search(rf'{re.escape(version)}\s+build\s+(\d+)', text, re.IGNORECASE)
                    if build_match:
                        builds_found.append((build_match.group(1), 'build_suffix'))
            
            # Return the lowest build number (patches are typically for initial builds)
            if builds_found:
                # Sort by build number (as integer) and return the lowest
                builds_found.sort(key=lambda x: int(x[0]))
                return builds_found[0]
    except Exception as e:
        logging.debug(f"Could not fetch build number: {e}")
    return None, None

def discover_app_main_url(config: dict) -> str | None:
    """Use APKMirror's search endpoint to discover the correct main app page URL when
    the configured 'org/name' combination doesn't match APKMirror's actual URL slugs.
    
    For example, config has org='duolingo', name='duolingo' but the actual page is at
    /apk/duolingo/duolingo-duolingo/. This function searches APKMirror and finds the
    correct main page URL by matching the org and the package name (most reliable).
    
    Returns the full main page URL if found, or None if discovery fails."""
    try:
        org = config.get('org', '')
        name = config.get('name', '')
        package = config.get('package', '')
        
        # Build search query - use package name if available (most precise), else app name
        # Strip ".apk" or trailing dashes from name for cleaner search
        query_terms = []
        if package:
            query_terms.append(package)
        if name:
            query_terms.append(name.replace('-', ' '))
        
        for query in query_terms:
            search_url = f"{base_url}/?post_type=app_release&searchtype=app&s={quote(query)}"
            logging.info(f"Searching APKMirror for app: {search_url}")
            
            try:
                response = _cf_get(search_url)
                if response.status_code != 200:
                    continue
                
                soup = BeautifulSoup(response.content, "html.parser")
                
                # Find all /apk/{org}/{slug}/ links - these are candidate main app pages
                # We prioritize matches under the same 'org' as the config
                found_links = set()
                for link in soup.find_all('a', href=True):
                    href = link['href']
                    # Match pattern /apk/{org}/{slug}/ but NOT /apk/{org}/{slug}/{anything-else}
                    m = re.match(r'^(/apk/[a-z0-9._-]+/[a-z0-9._-]+/)$', href)
                    if m:
                        found_links.add(m.group(1))
                
                if not found_links:
                    continue
                
                # Prefer links under the configured org
                org_links = [link for link in found_links if link.startswith(f"/apk/{org}/")]
                
                # Among org-matching links, find the one most likely to be the right app
                # Strategy: pick one whose slug contains the configured name as a substring
                # If multiple, prefer the shorter slug (more "exact" match)
                candidates = org_links if org_links else list(found_links)
                
                # Filter candidates: prefer those containing 'name' in the slug
                name_matches = [link for link in candidates if name and name in link]
                if name_matches:
                    candidates = name_matches
                
                # Sort by slug length (shorter = more specific match)
                candidates.sort(key=lambda x: len(x))
                
                if candidates:
                    discovered = urljoin(base_url + "/", candidates[0])
                    logging.info(f"✓ Discovered main app page via search: {discovered}")
                    return discovered
            except Exception as e:
                logging.debug(f"Error during search query '{query}': {e}")
                continue
        
        logging.debug("No matching app found via search")
        return None
        
    except Exception as e:
        logging.debug(f"Error in discover_app_main_url: {e}")
        return None

def _scrape_release_url_from_soup(soup, version: str, config: dict, build_number: str = None, build_format: str = None) -> str | None:
    """Scan a BeautifulSoup-parsed main app page for a release link matching the version.
    Returns the full release page URL if found, else None."""
    # Generate version variations (original + stripped of leading zeros like 26.04.05 -> 26.4.5)
    version_variants = [version]
    clean_v = ".".join(str(int(p)) if p.isdigit() else p for p in version.split('.'))
    if clean_v != version:
        version_variants.append(clean_v)

    app_slug = (config.get('name') or config.get('app_slug') or '').lower()

    for v in version_variants:
        version_parts = v.split('.')
        min_depth = 2 if len(version_parts) >= 2 else 1
        
        for i in range(len(version_parts), min_depth - 1, -1):
            current_ver = ".".join(version_parts[:i])
            current_ver_dash = "-".join(version_parts[:i])
            
            candidates = []
            for link in soup.find_all('a', href=True):
                href = link['href'].lower()
                if not href.startswith('/apk/'):
                    continue
                # Ensure the link belongs to this app
                if app_slug and app_slug not in href:
                    continue
                # Check version pattern properly bounded
                ver_pattern = re.escape(current_ver_dash)
                if re.search(rf'(?:^|[/-]){ver_pattern}(?:[/-]|$)', href):
                    priority = 0 if href.rstrip('/').endswith('-release') else 1
                    candidates.append((priority, link['href']))
            
            if candidates:
                candidates.sort(key=lambda x: (x[0], len(x[1])))
                chosen = candidates[0][1]
                full_url = urljoin(base_url + "/", chosen)
                logging.info(f"✓ Found release page on main listing for {current_ver}: {full_url}")
                return full_url
    
    return None

def find_release_page_from_main(version: str, config: dict, build_number: str = None, build_format: str = None) -> str | None:
    """Scrape the main app listing page on APKMirror to find the correct release page URL
    for a specific version. This avoids URL construction from config fields, which may not
    match APKMirror's actual URL slugs (e.g., 'duolingo' vs 'duolingo-language-lessons').
    
    Strategy:
    1. Try the configured main page (org/name from config)
    2. If that 404s, use APKMirror search to discover the correct main page URL
    3. Scrape release links from whichever main page works
    
    Returns the full release page URL if found, or None if scraping fails."""
    try:
        # Step 1: Try configured main page first (works for most apps)
        main_url = f"{base_url}/apk/{config['org']}/{config['name']}/"
        response = _cf_get(main_url)
        
        soup = None
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            result = _scrape_release_url_from_soup(soup, version, config, build_number, build_format)
            if result:
                return result
            logging.debug(f"Main page accessible but no version match: {main_url}")
        else:
            logging.info(f"Configured main page returned {response.status_code}: {main_url}")
        
        # Step 2: If configured main page failed or didn't yield a match, try discovering
        # the correct main page via APKMirror's search endpoint
        discovered_url = discover_app_main_url(config)
        if discovered_url and discovered_url != main_url:
            logging.info(f"Trying discovered main page: {discovered_url}")
            response = _cf_get(discovered_url)
            if response.status_code == 200:
                soup = BeautifulSoup(response.content, "html.parser")
                result = _scrape_release_url_from_soup(soup, version, config, build_number, build_format)
                if result:
                    return result
        
        logging.debug(f"Could not find release page URL from main listing for version {version}")
        return None
        
    except Exception as e:
        logging.debug(f"Error scraping main page for release URL: {e}")
        return None

def _normalize_release_lookup_version(version: str, target_arch: str) -> str:
    """Normalize Morphe's variant-qualified version for APKMirror release lookup.

    Some patch sources declare an APK variant as the compatible version, e.g.
    "18.0.3.954559732-release-arm64-v8a". APKMirror uses that full string for
    the variant identity, but its release page/API identifies the release as
    "18.0.3.954559732". Passing the variant-qualified value into the release
    resolver makes it construct a non-existent release URL and can also trigger
    a false API release-mismatch error.

    Only remove the exact architecture suffix for the requested architecture;
    do not strip other release channels such as beta/lite.
    """
    value = str(version or "").strip()
    arch = str(target_arch or "").strip()
    if not value or not arch:
        return value

    suffix = rf"-release-{re.escape(arch)}$"
    normalized = re.sub(suffix, "", value, flags=re.IGNORECASE)
    if normalized != value:
        logging.info(
            "APKMirror normalized variant version %s -> release version %s",
            value,
            normalized,
        )
    return normalized

def get_download_link(version: str, app_name: str, config: dict, arch: str = None) -> str:
    global _blocked_by_cloudflare
    _blocked_by_cloudflare = False
    if not version:
        logging.error(f"No version provided for {app_name}")
        return None
        
    target_arch = arch if (arch and arch != "universal") else config.get('arch', 'universal')
    version = _normalize_release_lookup_version(version, target_arch)
    
    criteria = [config['type'], target_arch, config['dpi']]
    
    # --- UNIVERSAL URL FINDER WITH VALIDATION ---
    # Extract build number if present (e.g., "32.30.0(1575420)" -> version="32.30.0", build="1575420")
    build_number = None
    build_format = None
    
    # Parse only build-number syntax already present in the configured version.
    # Do not visit the APKMirror app landing page before trying the exact release
    # URL; pinned releases usually do not need a separately discovered build number.
    build_match = re.search(r'\((\d+)\)$', version)
    if build_match:
        build_number = build_match.group(1)
        build_format = 'parentheses'
        version = version[:build_match.start()]
    else:
        build_match = re.search(r'\s+build\s+(\d+)$', version, re.IGNORECASE)
        if build_match:
            build_number = build_match.group(1)
            build_format = 'build_suffix'
            version = version[:build_match.start()]

    version_parts = version.split('.')
    found_soup = None
    found_release_url = None
    correct_version_page = False

    # --- PRIMARY APPROACH: APKMirror client API ---
    # Resolve candidate variants through APKMirror's official client endpoint.
    # This avoids the app landing/release pages that are being Cloudflare-blocked
    # on GitHub-hosted runners.
    api_variants = _get_api_variant_urls(version, config, target_arch)
    for api_variant_url, api_version in api_variants:
        if _blocked_by_cloudflare:
            logging.warning(
                "APKMirror is blocked by Cloudflare for this runner; "
                "stopping all remaining APKMirror variants and returning control to the next provider."
            )
            return None

        logging.info(f"✓ APKMirror API variant candidate: {api_variant_url}")
        direct_file_url, readable = _download_from_variant_page(
            api_variant_url,
            config,
            version,
        )
        if direct_file_url:
            return direct_file_url

        if _blocked_by_cloudflare:
            logging.warning(
                "APKMirror Cloudflare block is persistent; abandoning APKMirror "
                "without trying additional variants."
            )
            return None

        if not readable:
            logging.warning(
                "APKMirror API variant was not readable; trying the next variant."
            )

    # --- SECONDARY APPROACH: Direct release URL ---
    if not correct_version_page:
        direct_soup, direct_url = _get_direct_release_page(version, config)
        if direct_soup is not None:
            found_soup = direct_soup
            found_release_url = direct_url
            correct_version_page = True

    # A few APKMirror releases encode a build number in their listing, while the
    # configured patch version may omit it. Only query the app page for that
    # additional build-number detail when direct resolution did not work.
    if not correct_version_page and not _blocked_by_cloudflare:
        build_number, build_format = get_build_number_for_version(version, config)
        if build_number:
            logging.info(
                f"Found build number {build_number} for version {version} (format: {build_format})"
            )
            direct_soup, direct_url = _get_direct_release_page(version, config)
            if direct_soup is not None:
                found_soup = direct_soup
                found_release_url = direct_url
                correct_version_page = True

    # --- SECONDARY APPROACH: Scrape the main app page for the correct release URL ---
    # This is more reliable than constructing URLs from config fields, because
    # APKMirror's actual URL slugs often differ from config values
    # (e.g., 'duolingo' slug vs 'duolingo-language-lessons' actual release name)
    if not correct_version_page and not _blocked_by_cloudflare:
        scraped_url = find_release_page_from_main(version, config, build_number, build_format)
        if scraped_url:
            logging.info(f"Trying scraped release URL: {scraped_url}")
            try:
                response = _cf_get(scraped_url, referer=f"{base_url}/")
                if response.status_code == 200:
                    soup = BeautifulSoup(response.content, "html.parser")
                    page_text = soup.get_text()
                    # Quick validation: check version appears on page
                    if version in page_text or version.replace('.', '-') in page_text:
                        logging.info(f"✓ Scraped release page validated: {response.url}")
                        found_soup = soup
                        found_release_url = response.url
                        correct_version_page = True
                    else:
                        logging.warning(
                            f"Scraped URL returned page but version {version} not found in content"
                        )
            except Exception as e:
                logging.warning(f"Error fetching scraped URL: {e}")

    # Once Cloudflare has challenged this runner, generated URL probes cannot
    # succeed. Stop here so one app does not emit misleading 404s for every
    # possible release slug and the configured fallback can run immediately.
    if _blocked_by_cloudflare:
        return None
    
    # --- FALLBACK: Construct URLs from config fields ---
    # Only used if scraping the main page didn't work
    if not correct_version_page:
        logging.info("Scraping didn't find the page, falling back to URL construction...")
        # Use release_prefix if available, otherwise use app name
        release_name = config.get('release_prefix', config['name'])
        app_slugs = _app_slug_candidates(config)
        
        # Loop backwards: Try full version, then strip parts
        for i in range(len(version_parts), 0, -1):
            current_ver_str = "-".join(version_parts[:i])
            
            # If build number exists, append it to the last version part in URL
            if build_number and i == len(version_parts):
                if build_format == 'build_suffix':
                    # e.g., "6-6" + "build-006" -> "6-6-build-006"
                    current_ver_str = current_ver_str + "-build-" + build_number
                else:
                    # e.g., "32-30-0" + "1575420" -> "32-30-01575420"
                    parts = version_parts[:i]
                    parts[-1] = parts[-1] + build_number
                    current_ver_str = "-".join(parts)
            
            # Generate ALL possible URL patterns in priority order
            url_patterns = []
            
            # URL-encode the release_name to handle unicode characters like ․
            encoded_release_name = quote(release_name, safe='')
            org = config.get('org', '')
            encoded_org = quote(org, safe='')

            for app_slug in app_slugs:
                encoded_name = quote(app_slug, safe='')

                # Prefer the explicit release slug; it is more stable than a
                # display name and supports apps whose title changes over time.
                url_patterns.append(f"{base_url}/apk/{org}/{encoded_name}/{encoded_release_name}-{current_ver_str}-release/")

                if release_name != app_slug:
                    url_patterns.append(f"{base_url}/apk/{org}/{encoded_name}/{encoded_name}-{current_ver_str}-release/")

                if org and org != release_name and org != app_slug:
                    url_patterns.append(f"{base_url}/apk/{org}/{encoded_name}/{encoded_org}-{current_ver_str}-release/")

                url_patterns.append(f"{base_url}/apk/{org}/{encoded_name}/{encoded_release_name}-{current_ver_str}/")

                if release_name != app_slug:
                    url_patterns.append(f"{base_url}/apk/{org}/{encoded_name}/{encoded_name}-{current_ver_str}/")

                if org and org != release_name and org != app_slug:
                    url_patterns.append(f"{base_url}/apk/{org}/{encoded_name}/{encoded_org}-{current_ver_str}/")
            
            # Remove duplicate patterns
            url_patterns = list(dict.fromkeys(url_patterns))
            
            for url in url_patterns:
                logging.info(f"Checking potential release URL: {url}")
                
                try:
                    response = _cf_get(url)
                    if response.status_code == 200:
                        soup = BeautifulSoup(response.content, "html.parser")
                        page_text = soup.get_text()
                        
                        # VALIDATION: Check if this page is for our EXACT version
                        # Check multiple possible version formats
                        version_checks = [
                            version,  # 6.6
                            version.replace('.', '-'),  # 6-6
                            current_ver_str,  # 6-6-build-002 (if stripped)
                            ".".join(version_parts[:i])  # 6.6 (if stripped)
                        ]
                        
                        # Add build suffix format if we have a build number
                        if build_number:
                            if build_format == 'build_suffix':
                                version_checks.append(f"{version} build {build_number}")  # 6.6 build 002
                                version_checks.append(f"{version.replace('.', '-')}-build-{build_number}")  # 6-6-build-002
                            else:
                                version_checks.append(f"{version}({build_number})")  # 32.30.0(1575420)
                        
                        # Also check page title and headings for version
                        title_tag = soup.find('title')
                        headings = soup.find_all(['h1', 'h2', 'h3'])
                        
                        is_correct_page = False
                        
                        # Check in page text
                        for check in version_checks:
                            if check and check in page_text:
                                # Accept version match if it's the base version or includes build info
                                if check == version or check == version.replace('.', '-') or check == current_ver_str:
                                    is_correct_page = True
                                    break
                        
                        # Check in title and headings
                        if not is_correct_page:
                            for heading in headings:
                                heading_text = heading.get_text()
                                for check in version_checks:
                                    if check and check in heading_text:
                                        is_correct_page = True
                                        break
                                if is_correct_page:
                                    break
                        
                        if not is_correct_page and title_tag:
                            title_text = title_tag.get_text()
                            for check in version_checks:
                                if check and check in title_text:
                                    is_correct_page = True
                                    break
                        
                        if is_correct_page:
                            content_size = len(response.content)
                            logging.info(f"✓ Correct version page found: {response.url}")
                            found_soup = soup
                            correct_version_page = True
                            break  # Found correct page!
                        else:
                            # Page exists but doesn't have our version as primary
                            logging.warning(f"Page found but not for version {version}: {url}")
                            # Save as fallback ONLY if we haven't found any page yet
                            if found_soup is None:
                                found_soup = soup
                                logging.warning(f"Saved as fallback page (may list multiple versions)")
                            continue
                            
                    elif response.status_code == 404:
                        logging.info(f"URL not found (404): {url}")
                        continue
                    else:
                        logging.warning(f"URL {url} returned status {response.status_code}")
                        continue
                        
                except Exception as e:
                    logging.warning(f"Error checking {url}: {str(e)[:50]}")
                    continue
            
            if correct_version_page:
                break  # Found correct page for this version part
    
    # If we didn't find the exact version page but found a fallback
    if not correct_version_page and found_soup:
        logging.warning(f"Using fallback page for {app_name} {version} (may contain multiple versions)")
    
    if not found_soup:
        logging.error(f"Could not find any release page for {app_name} {version}")
        return None
    
    # --- VARIANT FINDER (works with both exact pages and fallback pages) ---
    rows = found_soup.find_all('div', class_='table-row')
    if not rows:
        table = found_soup.find('div', class_='variants-table')
        if table:
            rows = table.find_all('div', class_='table-row')
    download_page_url = None
    
    def _row_matches(row_text: str, allow_bundle: bool = True) -> bool:
        r = row_text.lower()
        if not _variant_matches_extra_criteria(row_text, config, version):
            return False
        if 'variant' in r and 'arch' in r and 'version' in r:
            return False  # Skip header row
            
        c_type = (config.get('type') or '').lower()
        if c_type:
            if c_type == 'apk':
                # A real APK variant is preferred, but some APK-configured apps
                # are only exposed by APKMirror as bundle rows. In that case the
                # caller may intentionally accept the bundle as a fallback.
                if not allow_bundle and 'bundle' in r:
                    return False
                if not allow_bundle and not re.search(r'\bapk\b', r):
                    return False
            elif c_type == 'bundle':
                if 'bundle' not in r:
                    return False
            elif c_type not in r:
                return False
        
        t_arch = (target_arch or 'universal').lower()
        if t_arch in ['universal', 'noarch']:
            if not any(a in r for a in ['universal', 'noarch', 'arm64-v8a', 'armeabi-v7a', 'arm64', 'arm']):
                return False
        elif t_arch not in r:
            # APKMirror's web table can expose a multi-ABI APKM/APKS as
            # "universal" even though its bundle contents include the requested
            # architecture. Do NOT broaden this to arbitrary universal APKs:
            # only a row explicitly identified as a bundle may use this
            # secondary match. Exact ABI rows remain preferred because they are
            # checked first by the caller.
            universal_bundle = (
                re.search(r'\b(?:universal|noarch)\b', r)
                and re.search(r'\bbundle\b', r)
            )
            if not universal_bundle:
                return False
            logging.info(
                "APKMirror: accepting universal bundle as fallback for requested ABI %s",
                t_arch,
            )

        c_dpi = (config.get('dpi') or 'nodpi').lower()
        if c_dpi in ['nodpi', '120-640dpi', 'all', '']:
            # All DPIs acceptable for universal/nodpi/bundle
            pass
        else:
            # APKMirror often splits the same bundle across overlapping DPI
            # ranges (for example 360-480dpi / 560-640dpi). A configured
            # range such as 240-640dpi means any variant fully contained in
            # that range is compatible; do not require the literal range
            # string to appear in the row.
            def _dpi_range(value):
                m = re.search(r'(?<!\d)(\d+)\s*[-–]\s*(\d+)\s*dpi', value)
                return (int(m.group(1)), int(m.group(2))) if m else None

            configured_dpi = _dpi_range(c_dpi)
            row_dpi = _dpi_range(r)
            if configured_dpi and row_dpi:
                if row_dpi[0] < configured_dpi[0] or row_dpi[1] > configured_dpi[1]:
                    return False
            elif c_dpi not in r:
                return False

        return True

    # Helper to find download link in a row
    def _extract_row_link(row) -> str | None:
        for a in row.find_all('a', href=True):
            href = a['href']
            if '-android-apk-download' in href or href.rstrip('/').endswith('-download'):
                if not href.endswith('#disqus_thread'):
                    return urljoin(base_url + "/", href)
        link = row.find('a', class_='accent_color')
        if link and 'href' in link.attrs and not link['href'].endswith('#disqus_thread'):
            return base_url + link['href']
        return None

    # Try to find exact version match first
    for row in rows:
        row_text = row.get_text()
        if 'variant' in row_text.lower() and 'arch' in row_text.lower():
            continue
        
        # Check if row contains our exact version
        if version in row_text or version.replace('.', '-') in row_text:
            if _row_matches(row_text, allow_bundle=False):
                download_page_url = _extract_row_link(row)
                if download_page_url:
                    break
    
    # If exact version not found, try to find any variant matching criteria
    if not download_page_url:
        for row in rows:
            row_text = row.get_text()
            if 'variant' in row_text.lower() and 'arch' in row_text.lower():
                continue
            if _row_matches(row_text, allow_bundle=True):
                # Check if this looks like a variant row (has version numbers)
                if re.search(r'\d+(\.\d+)+', row_text):
                    download_page_url = _extract_row_link(row)
                    if download_page_url:
                        match = re.search(r'(\d+(\.\d+)+(\.\w+)*)', row_text)
                        if match:
                            actual_version = match.group(1)
                            logging.warning(f"Using variant {actual_version} (criteria match)")
                        break
    
    if not download_page_url:
        logging.error(f"No variant found for {app_name} {version} with criteria {criteria}")
        # Debug: log what rows we found
        logging.debug(f"Found {len(rows)} rows total")
        for idx, row in enumerate(rows[:5]):  # First 5 rows
            logging.debug(f"Row {idx}: {row.get_text()[:100]}...")
        return None
    
    # --- STANDARD DOWNLOAD FLOW ---
    try:
        response = _cf_get(
            download_page_url,
            referer=found_release_url or f"{base_url}/",
        )
        response.raise_for_status()
        content_size = len(response.content)
        logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> Variant Page")
        soup = BeautifulSoup(response.content, "html.parser")

        sub_url = soup.find('a', class_='downloadButton')
        if sub_url:
            final_download_page_url = urljoin(base_url + "/", sub_url['href'])
            # Keep APKMirror's native APKM/APKS bundle intact. Morphe can
            # patch the bundle directly, so do not request forcebaseapk or
            # convert a split bundle into a standalone base APK here.
            response = _cf_get(
                final_download_page_url,
                referer=download_page_url,
            )
            response.raise_for_status()
            content_size = len(response.content)
            logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> Download Page")
            soup = BeautifulSoup(response.content, "html.parser")

            button = soup.find('a', id='download-link')
            if button:
                return urljoin(base_url + "/", button['href'])
    except Exception as e:
        logging.error(f"Error in download flow: {e}")

    return None

def get_architecture_criteria(arch: str) -> dict:
    """Map architecture names to APKMirror criteria"""
    arch_mapping = {
        "arm64-v8a": "arm64-v8a",
        "armeabi-v7a": "armeabi-v7a", 
        "universal": "universal"
    }
    return arch_mapping.get(arch, "universal")
    
def get_latest_version(app_name: str, config: dict) -> str:
    global _blocked_by_cloudflare
    _blocked_by_cloudflare = False
    # First try: get from main app page (e.g. /apk/org/name/)
    try:
        main_url = f"{base_url}/apk/{config['org']}/{config['name']}/"
        response = _cf_get(main_url)
        if response.status_code == 200:
            soup = BeautifulSoup(response.content, "html.parser")
            for h5 in soup.find_all("h5", class_="appRowTitle"):
                row_text = h5.get_text(strip=True)
                if any(skip in row_text.lower() for skip in ["wear os", "daydream", "automotive", "android tv", "beta", "alpha"]):
                    continue
                match = re.search(r'(\d+(\.\d+)+)', row_text)
                if match:
                    return match.group(1)
    except Exception:
        pass
    
    # Original method (keep exactly as you had it)
    url = f"{base_url}/uploads/?appcategory={config['name']}"
    
    response = _cf_get(url)
    response.raise_for_status()
    content_size = len(response.content)
    logging.info(f"URL:{response.url} [{content_size}/{content_size}] -> \"-\" [1]")
    soup = BeautifulSoup(response.content, "html.parser")

    app_rows = soup.find_all("div", class_="appRow")
    version_pattern = re.compile(r'\d+(\.\d+)*(-[a-zA-Z0-9]+(\.\d+)*)*')

    for row in app_rows:
        title_h5 = row.find("h5", class_="appRowTitle")
        if not title_h5 or not title_h5.a:
            continue
        version_text = title_h5.a.get_text(strip=True) or ""
        if "alpha" not in version_text.lower() and "beta" not in version_text.lower():
            match = version_pattern.search(version_text)
            if match:
                version = match.group()
                version_parts = version.split('.')
                base_version_parts = []
                for part in version_parts:
                    if part.isdigit():
                        base_version_parts.append(part)
                    else:
                        break
                if base_version_parts:
                    base_version = '.'.join(base_version_parts)
                    
                    # Check for build number in parentheses like "32.30.0(1575420)"
                    build_match = re.search(r'\((\d+)\)', version_text)
                    if build_match:
                        build_number = build_match.group(1)
                        return f"{base_version}({build_number})"
                    
                    return base_version

    return None