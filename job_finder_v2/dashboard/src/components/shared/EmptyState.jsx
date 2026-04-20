export default function EmptyState({ icon: Icon, title, description }) {
  return (
    <div className="flex flex-col items-center justify-center py-16 text-center">
      {Icon && <Icon className="mb-4 h-12 w-12 text-slate-500" />}
      {title && (
        <h3 className="mb-1 text-lg font-medium text-slate-300">{title}</h3>
      )}
      {description && (
        <p className="max-w-sm text-sm text-slate-400">{description}</p>
      )}
    </div>
  );
}
