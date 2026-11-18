import type { SkillDisplayCategory } from "../api/types";
import "./skill-category-fields.css";

export const skillCategoryOptions: { value: SkillDisplayCategory; label: string }[] = [
  { value: "content", label: "内容创作" },
  { value: "visual", label: "视觉表现" },
  { value: "processing", label: "素材处理" },
  { value: "rules", label: "规则约束" },
];

export function isSkillDisplayCategory(value: unknown): value is SkillDisplayCategory {
  return skillCategoryOptions.some(option => option.value === value);
}

export function SkillCategoryFields({ categories, onChange, disabled, focusFirst, legend = "Skill 分类（可选，可多选）" }: {
  categories: SkillDisplayCategory[];
  onChange: (categories: SkillDisplayCategory[]) => void;
  disabled?: boolean;
  focusFirst?: boolean;
  legend?: string;
}) {
  return <fieldset className="skill-category-fields" disabled={disabled}>
    <legend>{legend}</legend>
    <div className="skill-category-choices">
      {skillCategoryOptions.map(({ value, label }, index) => <label key={value}>
        <input type="checkbox" data-initial-focus={focusFirst && index === 0 ? true : undefined}
          checked={categories.includes(value)} onChange={event => onChange(event.target.checked
            ? [...categories, value] : categories.filter(category => category !== value))} />
        {label}
      </label>)}
    </div>
  </fieldset>;
}
