<script setup>
// Static, minimal onboarding background. The original canvas particle
// network + animated aurora read as "sci-fi quirky"; replaced with a
// subtle vignette spotlight behind the card and a barely-visible grid
// that fades to transparent at the edges. No animation, no particles,
// no neon — pure restrained fintech aesthetic.
</script>

<template>
  <div class="absolute inset-0 overflow-hidden pointer-events-none">
    <!-- Soft vignette: a single dark-blue radial that puts visual focus
         behind the card. No animation, no purple, no cyan blobs. -->
    <div
      class="absolute inset-0"
      style="
        background:
          radial-gradient(60% 55% at 50% 45%, rgba(30, 41, 59, 0.55), transparent 75%),
          radial-gradient(80% 70% at 50% 100%, rgba(15, 23, 42, 0.6), transparent 70%);
      "
    ></div>

    <!-- Faint grid for depth. Opacity 0.025 + radial mask means it's
         only visible toward the center where focus already is. -->
    <div
      class="absolute inset-0 opacity-[0.025]"
      style="
        background-image:
          linear-gradient(rgba(255, 255, 255, 0.6) 1px, transparent 1px),
          linear-gradient(90deg, rgba(255, 255, 255, 0.6) 1px, transparent 1px);
        background-size: 96px 96px;
        mask-image: radial-gradient(ellipse at center, black 25%, transparent 75%);
        -webkit-mask-image: radial-gradient(ellipse at center, black 25%, transparent 75%);
      "
    ></div>
  </div>
</template>
