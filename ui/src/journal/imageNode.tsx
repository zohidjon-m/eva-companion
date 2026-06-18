import Image from "@tiptap/extension-image";
import {
  NodeViewWrapper,
  ReactNodeViewRenderer,
  type NodeViewProps,
} from "@tiptap/react";

/**
 * CaptionedImage — the Image node rendered as a Medium-style <figure> with an
 * optional caption underneath.
 *
 * The caption is stored in the image's ``alt`` attribute, so the whole thing
 * still serialises to plain Markdown (``![caption](src)``) and the L0 file stays
 * the source of truth. While editing, the caption is a real input bound to the
 * node; read-only, it renders as a <figcaption> (or nothing when empty).
 */

function ImageNodeView({ node, updateAttributes, editor, selected }: NodeViewProps) {
  const src = (node.attrs.src as string) ?? "";
  const alt = (node.attrs.alt as string) ?? "";
  const editable = editor.isEditable;

  return (
    <NodeViewWrapper
      as="figure"
      className={`jimg${selected ? " jimg--selected" : ""}`}
    >
      <img className="jimg__img" src={src} alt={alt} draggable={false} />
      {editable ? (
        <input
          className="jimg__caption-input"
          value={alt}
          placeholder="Add a caption…"
          // Keep keystrokes/selection inside the input, not the ProseMirror doc.
          contentEditable={false}
          onMouseDown={(e) => e.stopPropagation()}
          onChange={(e) => updateAttributes({ alt: e.target.value })}
        />
      ) : alt ? (
        <figcaption className="jimg__caption">{alt}</figcaption>
      ) : null}
    </NodeViewWrapper>
  );
}

/** Image node that renders through {@link ImageNodeView} (block, no base64). */
export const CaptionedImage = Image.extend({
  addNodeView() {
    return ReactNodeViewRenderer(ImageNodeView);
  },
});
