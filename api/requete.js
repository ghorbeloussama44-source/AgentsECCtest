// Fonction Vercel : enregistre une demande Dialagram comme issue GitHub étiquetée « dialagram » + « a-faire ».
// Variables d'environnement à définir dans Vercel : GITHUB_TOKEN (droit « Issues : lecture et écriture » sur le dépôt)
// et ACCESS_CODE (code d'accès partagé avec les personnes autorisées).
const OWNER = "ghorbeloussama44-source";
const REPO = "AgentsECCtest";
const MODELES = ["auto", "qwen-3.8-max-thinking", "qwen-3.8-max", "qwen-3.7-max", "xiaomi-mimo-2.6", "meta-muse-spark-1.3"];

module.exports = async (req, res) => {
  if (req.method !== "POST") return res.status(405).json({ erreur: "Utilisez le formulaire de la page." });
  if (!process.env.GITHUB_TOKEN || !process.env.ACCESS_CODE)
    return res.status(503).json({ erreur: "Les demandes ne sont pas encore activées sur ce site (configuration manquante)." });

  const { titre = "", consigne = "", modele = "auto", nom = "", code = "" } = req.body || {};
  if (code !== process.env.ACCESS_CODE) return res.status(401).json({ erreur: "Code d'accès incorrect." });
  const t = String(titre).trim(), c = String(consigne).trim(), n = String(nom).trim().slice(0, 60);
  if (!t || t.length > 140) return res.status(400).json({ erreur: "Donnez un titre de 140 caractères au plus." });
  if (!c || c.length > 8000) return res.status(400).json({ erreur: "La consigne est vide ou dépasse 8 000 caractères." });
  const m = MODELES.includes(modele) ? modele : "auto";

  const body = [
    "## Consigne", "", c, "",
    "## Paramètres", "", `- Modèle demandé : \`${m}\``, `- Demandeur : ${n || "anonyme"}`, "",
    "_Demande envoyée depuis l'Atelier Dialagram. Claude la transmet à Dialagram, relit le résultat, le note et répond ici._",
  ].join("\n");

  const r = await fetch(`https://api.github.com/repos/${OWNER}/${REPO}/issues`, {
    method: "POST",
    headers: {
      Authorization: `Bearer ${process.env.GITHUB_TOKEN}`,
      Accept: "application/vnd.github+json",
      "Content-Type": "application/json",
      "User-Agent": "atelier-dialagram",
    },
    body: JSON.stringify({ title: `[Dialagram] ${t}`, body, labels: ["dialagram", "a-faire"] }),
  });
  if (!r.ok) return res.status(502).json({ erreur: "GitHub a refusé la demande. Vérifiez le jeton GITHUB_TOKEN dans Vercel." });
  const issue = await r.json();
  return res.status(201).json({ numero: issue.number, url: issue.html_url });
};
