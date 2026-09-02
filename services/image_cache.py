"""
services/image_cache.py — Downloads and caches Riot Data Dragon images.

Caches images locally to assets/img/champions/ and assets/img/items/.
Provides CTkImage objects directly for the UI.
"""
import json
import os
import logging
import threading
from typing import Optional

import requests
from PIL import Image
import customtkinter as ctk

from ui import image_utils as ImgU

logger = logging.getLogger(__name__)


class ImageCache:
    """Thread-safe image downloader and cacher for CustomTkinter."""

    _instance = None
    _lock = threading.Lock()

    def __new__(cls):
        with cls._lock:
            if cls._instance is None:
                cls._instance = super(ImageCache, cls).__new__(cls)
                cls._instance._init()
            return cls._instance

    def _init(self) -> None:
        self._champ_dir = os.path.join("assets", "img", "champions")
        self._item_dir  = os.path.join("assets", "img", "items")
        os.makedirs(self._champ_dir, exist_ok=True)
        os.makedirs(self._item_dir, exist_ok=True)

        self._cache: dict[str, ctk.CTkImage] = {}

        # Fetch Data Dragon version
        self._ddragon_version = "14.4.1"
        try:
            resp = requests.get("https://ddragon.leagueoflegends.com/api/versions.json", timeout=3)
            if resp.status_code == 200:
                self._ddragon_version = resp.json()[0]
        except Exception as e:
            logger.warning("Failed to fetch DDragon version: %s", e)

        # Champion: display name → image filename
        self._champ_image_map: dict[str, str] = {}
        try:
            with open("assets/champion_data.json", "r", encoding="utf-8") as f:
                data = json.load(f).get("data", {})
                for k, v in data.items():
                    name = v.get("name", "")
                    img_file = v.get("image", {}).get("full", f"{k}.png")
                    self._champ_image_map[name] = img_file
        except Exception as e:
            logger.warning("Failed to load champion_data.json: %s", e)

        # Item: display name -> image filename
        self._item_image_map: dict[str, str] = {}
        # Item: ID -> image filename
        self._item_id_map: dict[int, str] = {}
        # Item: ID -> display name
        self._item_id_to_name: dict[int, str] = {}
        self._item_id_to_gold: dict[int, int] = {}
        self._item_name_to_id: dict[str, int] = {}
        self._valid_items: set[str] = set()
        self._boots_ids: set[int] = set()
        suspects: dict[str, int] = {}

        try:
            with open("assets/item_data.json", "r", encoding="utf-8") as f:
                raw_items = json.load(f).get("data", {})

            for item_id, v in raw_items.items():
                name = v.get("name", "").strip()
                if not name:
                    continue

                # Filter rules for "complete, purchasable, in-store" items:
                gold         = v.get("gold", {})
                purchasable  = gold.get("purchasable", True)
                total_gold   = gold.get("total", 0)
                in_store     = v.get("inStore", True)
                has_from     = bool(v.get("from", []))  # has component items
                has_into     = bool(v.get("into", []))  # other items build out of it
                tags         = v.get("tags", [])
                depth        = v.get("depth", 1)
                maps         = v.get("maps", {})

                if not purchasable or not in_store:
                    continue

                # Add to image map regardless of tier (for UI display of components/starters)
                img_file = v.get("image", {}).get("full", f"{item_id}.png")
                self._item_image_map[name] = img_file
                if "Boots" in tags:
                    try:
                        self._boots_ids.add(int(item_id))
                    except ValueError:
                        pass
                try:
                    self._item_id_map[int(item_id)] = img_file
                    
                    if int(item_id) < 10000:
                        self._item_id_to_name[int(item_id)] = name
                        self._item_id_to_gold[int(item_id)] = total_gold
                        self._item_name_to_id[name] = int(item_id)
                    else:
                        suspects[name] = int(item_id)
                except ValueError:
                    pass

                if not maps.get("11", False):
                    continue                          # skip non-Summoner's Rift items (Arena, Nexus Blitz)
                
                if total_gold > 4000:
                    continue                          # skip Ornn upgrades (they cost 6000g+)

                if "Consumable" in tags or "Trinket" in tags:
                    continue
                
                if not has_from and total_gold >= 1000:
                    continue                          # skip legacy removed items and transformed items
                
                # Hard ban for items Riot refuses to remove from their API
                _REMOVED_ITEMS = {
                    "Lithoplastron de gargouille", 
                    "Pourfendeur divin", 
                    "Épée vespérale de Draktharr", 
                    "Éviscérateur", 
                    "Gel éternel",
                    "Couronne de la Reine brisée",
                    "Moissonneur nocturne",
                    "Putrificateur techno-chimique"
                }
                if name in _REMOVED_ITEMS:
                    continue
                
                is_boots = "Boots" in tags
                if has_into and not is_boots and depth < 3:
                    continue                          # skip components (they build into something else)

                if not has_from and total_gold < 1000:
                    continue                          # skip basic starters (Doran's, basic boots)

                self._valid_items.add(name)

            logger.info("ItemCache: %d complete items indexed.", len(self._valid_items))

        except Exception as e:
            logger.warning("Failed to load item_data.json: %s", e)

    # -----------------------------------------------------------------------
    # Internal
    # -----------------------------------------------------------------------

    def _get_image(self, url: str, path: str, size: tuple[int, int]) -> Optional[ctk.CTkImage]:
        cache_key = f"{path}_{size[0]}x{size[1]}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        if not os.path.exists(path):
            try:
                logger.debug("Downloading %s", url)
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    with open(path, "wb") as f:
                        f.write(resp.content)
                else:
                    return None
            except Exception as e:
                logger.warning("Failed to download %s: %s", url, e)
                return None

        try:
            img = Image.open(path)
            ctk_img = ctk.CTkImage(light_image=img, dark_image=img, size=size)
            self._cache[cache_key] = ctk_img
            return ctk_img
        except Exception as e:
            logger.warning("Failed to open image %s: %s", path, e)
            return None

    # -----------------------------------------------------------------------
    # Public API
    # -----------------------------------------------------------------------

    def get_item_name_by_id(self, item_id: int) -> str:
        """Return the canonical item name for a given item ID."""
        return self._item_id_to_name.get(item_id, f"Item_{item_id}")

    def is_boots(self, item_id: Optional[int]) -> bool:
        """True si l'objet est une paire de bottes (tag DDragon 'Boots')."""
        return bool(item_id) and int(item_id) in self._boots_ids

    def get_item_id_by_name(self, item_name: str) -> Optional[int]:
        return self._item_name_to_id.get(item_name, None)

    def get_item_gold_value(self, item_id: int) -> int:
        return self._item_id_to_gold.get(item_id, 0)

    def get_champion_image(self, champion_name: str, size: tuple[int, int] = (64, 64)) -> Optional[ctk.CTkImage]:
        if not champion_name or champion_name in ("—", "inconnu"):
            return None
        img_file = self._champ_image_map.get(champion_name)
        if not img_file:
            clean = champion_name.replace("'", "").replace(" ", "")
            img_file = f"{clean}.png"
        url  = f"https://ddragon.leagueoflegends.com/cdn/{self._ddragon_version}/img/champion/{img_file}"
        path = os.path.join(self._champ_dir, img_file)
        return self._get_image(url, path, size)

    def get_item_image(self, item_name: str, size: tuple[int, int] = (52, 52)) -> Optional[ctk.CTkImage]:
        if not item_name:
            return None
        img_file = self._item_image_map.get(item_name)
        if not img_file:
            return None
        url  = f"https://ddragon.leagueoflegends.com/cdn/{self._ddragon_version}/img/item/{img_file}"
        path = os.path.join(self._item_dir, img_file)
        return self._get_image(url, path, size)

    def is_valid_item(self, item_name: str) -> bool:
        """Returns True if item_name is a known complete, purchasable item."""
        return item_name.strip() in self._valid_items

    def fuzzy_match_item(self, name: str) -> Optional[str]:
        """
        Try to find the best matching valid item name for a string.
        Returns the canonical item name or None.
        """
        name = name.strip()
        all_items = self._item_image_map.keys()
        if name in all_items:
            return name
        # Case-insensitive exact match
        lower = name.lower()
        for valid in all_items:
            if valid.lower() == lower:
                return valid
        # Substring match (both ways)
        for valid in all_items:
            if lower in valid.lower() or valid.lower() in lower:
                return valid
        return None

    # -----------------------------------------------------------------------
    # Processed (PIL-enhanced) image API
    # -----------------------------------------------------------------------

    def get_champion_icon_round(
        self,
        champion_name: str,
        size: int = 64,
        ring_color: str = "#c8a840",
        glow: bool = True,
    ) -> ctk.CTkImage:
        """
        Return a circular champion icon with gold ring + glow halo.
        Falls back to a placeholder if the image cannot be loaded.
        """
        cache_key = f"round_{champion_name}_{size}_{glow}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        # Load raw PIL image
        raw = self._load_pil_champion(champion_name)
        if raw is None:
            img = ImgU.make_placeholder_champion(size)
        else:
            img = ImgU.make_champion_icon(
                raw, size=size, ring_color=ring_color, glow=glow
            )
        self._cache[cache_key] = img
        return img

    def get_item_icon_rounded(
        self,
        item_name: str,
        size: int = 48,
        is_filled: bool = False,
    ) -> ctk.CTkImage:
        """
        Return a rounded-corner item icon with dark (or gold) border.
        Falls back to a placeholder if the image cannot be loaded.
        """
        cache_key = f"rounded_{item_name}_{size}_{is_filled}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        raw = self._load_pil_item(item_name)
        if raw is None:
            img = ImgU.make_placeholder_item(size)
        else:
            img = ImgU.make_item_icon(raw, size=size, is_filled=is_filled)
        self._cache[cache_key] = img
        return img

    def get_item_icon_by_id(
        self,
        item_id: int,
        size: int = 48,
        is_filled: bool = False,
    ) -> ctk.CTkImage:
        """Return a rounded-corner item icon using the item ID."""
        cache_key = f"rounded_id_{item_id}_{size}_{is_filled}"
        if cache_key in self._cache:
            return self._cache[cache_key]

        raw = self._load_pil_item_by_id(item_id)
        if raw is None:
            img = ImgU.make_placeholder_item(size)
        else:
            img = ImgU.make_item_icon(raw, size=size, is_filled=is_filled)
        self._cache[cache_key] = img
        return img

    # ── Internal PIL loaders (bypasses CTkImage, returns raw PIL Image) ───

    def _load_pil_champion(self, champion_name: str) -> Optional[Image.Image]:
        """Download/load champion PNG and return raw PIL Image (RGBA)."""
        if not champion_name or champion_name in ("—", "inconnu"):
            return None
        img_file = self._champ_image_map.get(champion_name)
        if not img_file:
            clean = champion_name.replace("'", "").replace(" ", "")
            img_file = f"{clean}.png"
        path = os.path.join(self._champ_dir, img_file)
        url  = f"https://ddragon.leagueoflegends.com/cdn/{self._ddragon_version}/img/champion/{img_file}"
        return self._load_pil_raw(url, path)

    def _load_pil_item(self, item_name: str) -> Optional[Image.Image]:
        """Download/load item PNG and return raw PIL Image (RGBA)."""
        if not item_name:
            return None
        img_file = self._item_image_map.get(item_name)
        if not img_file:
            return None
        path = os.path.join(self._item_dir, img_file)
        url  = f"https://ddragon.leagueoflegends.com/cdn/{self._ddragon_version}/img/item/{img_file}"
        return self._load_pil_raw(url, path)

    def _load_pil_item_by_id(self, item_id: int) -> Optional[Image.Image]:
        """Download/load item PNG by ID and return raw PIL Image (RGBA)."""
        img_file = self._item_id_map.get(item_id)
        if not img_file:
            return None
        path = os.path.join(self._item_dir, img_file)
        url  = f"https://ddragon.leagueoflegends.com/cdn/{self._ddragon_version}/img/item/{img_file}"
        return self._load_pil_raw(url, path)

    def _load_pil_raw(self, url: str, path: str) -> Optional[Image.Image]:
        """Ensure the file exists locally (download if needed) and open as PIL."""
        if not os.path.exists(path):
            try:
                resp = requests.get(url, timeout=5)
                if resp.status_code == 200:
                    with open(path, "wb") as f:
                        f.write(resp.content)
                else:
                    return None
            except Exception as e:
                logger.warning("Failed to download %s: %s", url, e)
                return None
        try:
            return Image.open(path).convert("RGBA")
        except Exception as e:
            logger.warning("Failed to open image %s: %s", path, e)
            return None

    @property
    def valid_items(self) -> set[str]:
        return self._valid_items
