(() => {
  const segmenter = typeof Intl !== "undefined" && Intl.Segmenter
    ? new Intl.Segmenter("vi", { granularity: "grapheme" })
    : null;

  function splitCharacters(word) {
    return segmenter
      ? Array.from(segmenter.segment(word), part => part.segment)
      : Array.from(word.normalize("NFC"));
  }

  document.querySelectorAll(".hero-3d-line").forEach(line => {
    const words = line.textContent.trim().split(/\s+/u);
    line.replaceChildren();
    let characterIndex = 0;

    words.forEach(word => {
      const wordNode = document.createElement("span");
      wordNode.className = "hero-3d-word";
      wordNode.setAttribute("aria-hidden", "true");

      splitCharacters(word).forEach(character => {
        const characterNode = document.createElement("span");
        characterNode.className = "hero-3d-char";

        const cube = document.createElement("span");
        cube.className = "hero-3d-cube";
        cube.style.setProperty("--swap-delay", `${characterIndex * 0.03}s`);

        const front = document.createElement("span");
        front.className = "hero-3d-face hero-3d-face--front";
        front.textContent = character;

        const back = document.createElement("span");
        back.className = "hero-3d-face hero-3d-face--back";
        back.textContent = character;

        cube.append(front, back);
        characterNode.append(cube);
        wordNode.append(characterNode);
        characterIndex += 1;
      });

      line.append(wordNode);
    });
  });
})();
