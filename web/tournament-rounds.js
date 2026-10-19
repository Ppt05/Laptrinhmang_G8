// Name knockout rounds from the fixed bracket, including BYEs and vacant slots.
function tournamentRoundName(tournament, level) {
  const number = Number(level);
  if (!number) return "Vòng bảng";
  const bracket = tournament?.bracket;
  const matches = bracket?.size
    ? bracket.size / (2 ** number)
    : bracket?.levels?.[number]?.length;
  if (matches === 1) return "Chung kết";
  if (matches === 2) return "Bán kết";
  if (matches === 4) return "Tứ kết";
  return Number.isInteger(matches) && matches > 4
    ? `Vòng ${matches * 2} đội`
    : "Vòng loại trực tiếp";
}
