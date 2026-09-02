"""
ui/image_utils.py — PIL image processing for premium LoL-style icons.

All functions return CTkImage objects ready to drop into CTkLabel widgets.
Processing results are NOT cached here — use ImageCache for that.

Functions:
  make_champion_icon   — circular crop + gold ring + optional glow halo
  make_item_icon       — rounded corners + subtle dark border
  make_gradient_image  — horizontal gradient (for card header separators)
  hex_to_rgb           — convenience colour converter
"""
from __future__ import annotations


import customtkinter as ctk
from PIL import Image, ImageDraw, ImageFilter


# ---------------------------------------------------------------------------
# Colour helpers
# ---------------------------------------------------------------------------

def hex_to_rgb(hex_color: str) -> tuple[int, int, int]:
    """Convert '#rrggbb' to (r, g, b)."""
    h = hex_color.lstrip("#")
    return int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)


def hex_to_rgba(hex_color: str, alpha: int = 255) -> tuple[int, int, int, int]:
    r, g, b = hex_to_rgb(hex_color)
    return r, g, b, alpha


# ---------------------------------------------------------------------------
# Champion icon — circular with gold ring + glow halo
# ---------------------------------------------------------------------------

def make_champion_icon(
    pil_img: Image.Image,
    size: int = 64,
    ring_color: str = "#c8a840",
    ring_width: int = 3,
    glow: bool = True,
    glow_color: str = "#c8a840",
    glow_radius: int = 8,
    glow_alpha: int = 90,
    bg_color: str = "#07101e",
) -> ctk.CTkImage:
    """
    Produce a circular champion portrait with a gold ring and optional glow.

    Pipeline:
      1. Resize source image to (size × size)
      2. Apply circular alpha mask (smooth edges via high-res mask downsample)
      3. Build glow layer: blurred filled circle (GaussianBlur)
      4. Composite: glow → cropped circle → gold ring outline
      5. Return as CTkImage
    """
    PADDING = glow_radius * 2 if glow else ring_width * 2
    total = size + PADDING * 2

    # ── Step 1: resize source ──────────────────────────────────────────────
    src = pil_img.convert("RGBA").resize((size, size), Image.LANCZOS)

    # ── Step 2: circular mask (2× res for anti-aliasing, then downscale) ──
    mask_hr_size = size * 2
    mask_hr = Image.new("L", (mask_hr_size, mask_hr_size), 0)
    ImageDraw.Draw(mask_hr).ellipse(
        (0, 0, mask_hr_size - 1, mask_hr_size - 1), fill=255
    )
    mask = mask_hr.resize((size, size), Image.LANCZOS)
    src.putalpha(mask)

    # ── Step 3: glow layer ─────────────────────────────────────────────────
    canvas = Image.new("RGBA", (total, total), (0, 0, 0, 0))

    if glow:
        gr, gg, gb = hex_to_rgb(glow_color)
        glow_layer = Image.new("RGBA", (total, total), (0, 0, 0, 0))
        gd = ImageDraw.Draw(glow_layer)
        gd.ellipse(
            (PADDING - glow_radius, PADDING - glow_radius,
             PADDING + size + glow_radius, PADDING + size + glow_radius),
            fill=(gr, gg, gb, glow_alpha),
        )
        glow_layer = glow_layer.filter(ImageFilter.GaussianBlur(glow_radius))
        canvas = Image.alpha_composite(canvas, glow_layer)

    # ── Step 4: paste the circular portrait ───────────────────────────────
    canvas.paste(src, (PADDING, PADDING), src)

    # ── Step 5: gold ring ─────────────────────────────────────────────────
    ring_d = ImageDraw.Draw(canvas)
    ring_d.ellipse(
        (PADDING, PADDING, PADDING + size - 1, PADDING + size - 1),
        outline=ring_color,
        width=ring_width,
    )

    ctk_img = ctk.CTkImage(
        light_image=canvas,
        dark_image=canvas,
        size=(total, total),
    )
    return ctk_img


# ---------------------------------------------------------------------------
# Item icon — rounded corners + dark border
# ---------------------------------------------------------------------------

def make_item_icon(
    pil_img: Image.Image,
    size: int = 48,
    corner_radius: int = 6,
    border_color: str = "#1e3a5f",
    border_width: int = 2,
    filled_border_color: str = "#c8a840",
    is_filled: bool = False,
) -> ctk.CTkImage:
    """
    Produce an item icon with rounded corners and an optional gold border
    (used when the slot is 'filled' / active).

    Pipeline:
      1. Resize source image
      2. Build rounded-corner alpha mask
      3. Apply mask to get rounded square
      4. Draw border outline
      5. Return as CTkImage
    """
    # ── Step 1: resize ────────────────────────────────────────────────────
    src = pil_img.convert("RGBA").resize((size, size), Image.LANCZOS)

    # ── Step 2: rounded-corner mask (4× for anti-aliasing) ────────────────
    scale = 4
    hr_size = size * scale
    hr_radius = corner_radius * scale
    mask_hr = Image.new("L", (hr_size, hr_size), 0)
    md = ImageDraw.Draw(mask_hr)
    md.rounded_rectangle((0, 0, hr_size - 1, hr_size - 1), radius=hr_radius, fill=255)
    mask = mask_hr.resize((size, size), Image.LANCZOS)
    src.putalpha(mask)

    # ── Step 3: apply border ───────────────────────────────────────────────
    bd = ImageDraw.Draw(src)
    bc = filled_border_color if is_filled else border_color
    bd.rounded_rectangle(
        (0, 0, size - 1, size - 1),
        radius=corner_radius,
        outline=bc,
        width=border_width,
    )

    ctk_img = ctk.CTkImage(
        light_image=src,
        dark_image=src,
        size=(size, size),
    )
    return ctk_img


# ---------------------------------------------------------------------------
# Gradient bar — horizontal gold-to-transparent (for card headers)
# ---------------------------------------------------------------------------

def make_gradient_image(
    width: int,
    height: int,
    color_left: str = "#c8a840",
    color_right: str = "#0f1e30",
    alpha_left: int = 200,
    alpha_right: int = 0,
) -> ctk.CTkImage:
    """
    Return a horizontal gradient image for use as a card separator.
    Goes from `color_left` (opaque) to `color_right` (transparent).
    """
    rl, gl, bl = hex_to_rgb(color_left)
    rr, gr, br = hex_to_rgb(color_right)

    img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
    for x in range(width):
        t = x / max(width - 1, 1)
        r = int(rl + (rr - rl) * t)
        g = int(gl + (gr - gl) * t)
        b = int(bl + (br - bl) * t)
        a = int(alpha_left + (alpha_right - alpha_left) * t)
        for y in range(height):
            img.putpixel((x, y), (r, g, b, a))

    return ctk.CTkImage(light_image=img, dark_image=img, size=(width, height))


# ---------------------------------------------------------------------------
# Placeholder icon — shown when image is unavailable
# ---------------------------------------------------------------------------

def make_placeholder_champion(size: int = 64, bg_color: str = "#152538") -> ctk.CTkImage:
    """Grey circular placeholder for missing champion images."""
    PADDING = 8
    total = size + PADDING * 2
    canvas = Image.new("RGBA", (total, total), (0, 0, 0, 0))
    d = ImageDraw.Draw(canvas)
    r, g, b = hex_to_rgb(bg_color)
    d.ellipse(
        (PADDING, PADDING, PADDING + size - 1, PADDING + size - 1),
        fill=(r, g, b, 220),
        outline="#c8a840",
        width=2,
    )
    # "?" text centred
    d.text((total // 2 - 4, total // 2 - 8), "?", fill="#c8a840")
    return ctk.CTkImage(light_image=canvas, dark_image=canvas, size=(total, total))


def make_placeholder_item(size: int = 48) -> ctk.CTkImage:
    """Dark rounded-square placeholder for missing item images."""
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle((0, 0, size - 1, size - 1), radius=6,
                         fill="#152538", outline="#1e3a5f", width=2)
    d.text((size // 2 - 4, size // 2 - 8), "?", fill="#4a6680")
    return ctk.CTkImage(light_image=img, dark_image=img, size=(size, size))
