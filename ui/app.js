// WardenBot -- Phase 6a (2026-07-14) : rendu visuel + particules uniquement.
// Les boutons Start/Pause/Stop et la nav latérale sont INERTES ici -- pas
// encore de bridge Python (pywebview.api), ça arrive en Phase 6b/6c. Ce
// fichier ne fait QUE prouver que l'esthétique demandée s'affiche
// correctement avec des données statiques.

// --- Particules discrètes en fond (canvas, effet "subtle particle effects"
// du prompt) -- volontairement léger : peu de particules, mouvement lent. ---
(function initParticles() {
  const canvas = document.getElementById("particles");
  const ctx = canvas.getContext("2d");
  let width, height, particles;

  function resize() {
    width = canvas.width = window.innerWidth;
    height = canvas.height = window.innerHeight;
  }

  function makeParticles(count) {
    particles = Array.from({ length: count }, () => ({
      x: Math.random() * width,
      y: Math.random() * height,
      r: Math.random() * 1.4 + 0.3,
      vx: (Math.random() - 0.5) * 0.15,
      vy: (Math.random() - 0.5) * 0.15,
      alpha: Math.random() * 0.5 + 0.15,
    }));
  }

  function tick() {
    ctx.clearRect(0, 0, width, height);
    for (const p of particles) {
      p.x += p.vx;
      p.y += p.vy;
      if (p.x < 0) p.x = width;
      if (p.x > width) p.x = 0;
      if (p.y < 0) p.y = height;
      if (p.y > height) p.y = 0;
      ctx.beginPath();
      ctx.arc(p.x, p.y, p.r, 0, Math.PI * 2);
      ctx.fillStyle = `rgba(56, 226, 255, ${p.alpha})`;
      ctx.fill();
    }
    requestAnimationFrame(tick);
  }

  window.addEventListener("resize", () => {
    resize();
    makeParticles(70);
  });

  resize();
  makeParticles(70);
  tick();
})();

// --- Navigation latérale (bascule visuelle seule pour l'instant) ---
document.querySelectorAll(".nav-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".nav-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    // Phase 6b : chargera la vue correspondante (Calibration/Profiles/Logs).
    console.log(`[stub] navigation -> ${btn.dataset.view}`);
  });
});

// --- Boutons Start/Pause/Stop (stubs -- Phase 6c câblera le vrai contrôle) ---
document.getElementById("btn-start").addEventListener("click", () => {
  console.log("[stub] Start -- pas encore relié à RotationEngine");
});
document.getElementById("btn-pause").addEventListener("click", () => {
  console.log("[stub] Pause -- pas encore relié à RotationEngine");
});
document.getElementById("btn-stop").addEventListener("click", () => {
  console.log("[stub] Stop -- pas encore relié à RotationEngine");
});
