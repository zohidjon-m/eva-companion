import {
  forwardRef,
  useCallback,
  useEffect,
  useImperativeHandle,
  useRef,
  type ChangeEvent,
  type ReactNode,
} from "react";
import {
  BubbleMenu,
  EditorContent,
  FloatingMenu,
  useEditor,
  type Editor,
} from "@tiptap/react";
import StarterKit from "@tiptap/starter-kit";
import Link from "@tiptap/extension-link";
import Placeholder from "@tiptap/extension-placeholder";
import { Markdown } from "tiptap-markdown";
import { CaptionedImage } from "./imageNode";
import { uploadMedia } from "./api";

/**
 * JournalEditor — a Medium-style rich text editor that reads and writes Markdown.
 *
 * Two interactions mirror Medium: a bubble toolbar over a text selection
 * (bold / italic / link / headings / quote / code) and a "+" block menu on an
 * empty line (headings / lists / quote / code block / divider / photo). It
 * serialises to Markdown via tiptap-markdown so the L0 file stays the source of
 * truth, and uploads photos to the local vault (offline) before inserting them.
 *
 * Editable mode is uncontrolled — it starts from ``value`` and reports edits
 * through ``onChange``; read-only mode follows ``value`` for the entry view.
 */

export type JournalEditorHandle = {
  /** Insert plain text at the cursor (used by voice dictation). */
  insertText: (text: string) => void;
  focus: () => void;
};

type Props = {
  value: string; // Markdown (display form — absolute image URLs)
  editable: boolean;
  placeholder?: string;
  onChange?: (markdown: string) => void;
  onUploadError?: (message: string) => void;
};

const hasImageFiles = (files?: FileList | null): boolean =>
  !!files && Array.from(files).some((f) => f.type.startsWith("image/"));

export const JournalEditor = forwardRef<JournalEditorHandle, Props>(
  function JournalEditor(
    { value, editable, placeholder, onChange, onUploadError },
    ref,
  ) {
    const fileInputRef = useRef<HTMLInputElement>(null);

    const editor = useEditor({
      editable,
      extensions: [
        StarterKit.configure({ heading: { levels: [1, 2] } }),
        Link.configure({ openOnClick: false, autolink: true }),
        CaptionedImage,
        Placeholder.configure({
          placeholder: placeholder ?? "Write as much or as little as you like…",
        }),
        Markdown.configure({ html: false, transformPastedText: true }),
      ],
      content: value,
      editorProps: {
        attributes: { class: "jeditor__content" },
      },
      onUpdate: ({ editor }) =>
        onChange?.(editor.storage.markdown.getMarkdown()),
    });

    // Read-only view follows the incoming value (e.g. a post loads in async).
    // Editable mode is uncontrolled, so it ignores value after first mount.
    useEffect(() => {
      if (!editor || editable) return;
      editor.commands.setContent(value);
    }, [editor, editable, value]);

    useImperativeHandle(
      ref,
      () => ({
        insertText: (text: string) =>
          editor?.chain().focus().insertContent(text).run(),
        // Focus at the END of the doc, so editing an existing entry drops the
        // cursor ready to keep writing rather than at the very top.
        focus: () => editor?.chain().focus("end").run(),
      }),
      [editor],
    );

    const insertImageFiles = useCallback(
      async (files?: FileList | null) => {
        if (!editor || !files) return;
        for (const file of Array.from(files)) {
          if (!file.type.startsWith("image/")) continue;
          try {
            const { url } = await uploadMedia(file);
            editor.chain().focus().setImage({ src: url }).run();
          } catch (err) {
            onUploadError?.(
              err instanceof Error ? err.message : "Couldn't add that image.",
            );
          }
        }
      },
      [editor, onUploadError],
    );

    // Drag-and-drop and paste of images upload to the vault, then insert.
    useEffect(() => {
      if (!editor || !editable) return;
      const dom = editor.view.dom;
      const onDrop = (e: DragEvent) => {
        if (hasImageFiles(e.dataTransfer?.files)) {
          e.preventDefault();
          void insertImageFiles(e.dataTransfer?.files);
        }
      };
      const onPaste = (e: ClipboardEvent) => {
        if (hasImageFiles(e.clipboardData?.files)) {
          e.preventDefault();
          void insertImageFiles(e.clipboardData?.files);
        }
      };
      dom.addEventListener("drop", onDrop);
      dom.addEventListener("paste", onPaste);
      return () => {
        dom.removeEventListener("drop", onDrop);
        dom.removeEventListener("paste", onPaste);
      };
    }, [editor, editable, insertImageFiles]);

    const pickImage = () => fileInputRef.current?.click();
    const onFilePicked = (e: ChangeEvent<HTMLInputElement>) => {
      const files = e.target.files;
      e.target.value = ""; // allow re-picking the same file
      void insertImageFiles(files);
    };

    const setLink = () => {
      if (!editor) return;
      const prev = editor.getAttributes("link").href as string | undefined;
      const url = window.prompt("Link URL", prev ?? "https://");
      if (url === null) return;
      if (url.trim() === "") {
        editor.chain().focus().extendMarkRange("link").unsetLink().run();
        return;
      }
      editor
        .chain()
        .focus()
        .extendMarkRange("link")
        .setLink({ href: url.trim() })
        .run();
    };

    if (!editor) return <div className="jeditor" />;

    return (
      <div className="jeditor">
        {editable && (
          <>
            <BubbleMenu
              editor={editor}
              tippyOptions={{ duration: 120 }}
              shouldShow={({ editor, from, to }) =>
                from !== to && !editor.isActive("image")
              }
            >
              <div className="jmenu jmenu--bubble">
                <Btn on={editor.isActive("bold")} onClick={() => editor.chain().focus().toggleBold().run()} label="Bold">
                  <b>B</b>
                </Btn>
                <Btn on={editor.isActive("italic")} onClick={() => editor.chain().focus().toggleItalic().run()} label="Italic">
                  <i>i</i>
                </Btn>
                <Btn on={editor.isActive("link")} onClick={setLink} label="Link">
                  Link
                </Btn>
                <span className="jmenu__sep" />
                <Btn on={editor.isActive("heading", { level: 1 })} onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()} label="Heading 1">
                  H1
                </Btn>
                <Btn on={editor.isActive("heading", { level: 2 })} onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} label="Heading 2">
                  H2
                </Btn>
                <Btn on={editor.isActive("blockquote")} onClick={() => editor.chain().focus().toggleBlockquote().run()} label="Quote">
                  &ldquo;&rdquo;
                </Btn>
                <Btn on={editor.isActive("code")} onClick={() => editor.chain().focus().toggleCode().run()} label="Inline code">
                  &lt;/&gt;
                </Btn>
              </div>
            </BubbleMenu>

            <FloatingMenu editor={editor} tippyOptions={{ duration: 120 }}>
              <div className="jmenu jmenu--floating">
                <Btn onClick={() => editor.chain().focus().toggleHeading({ level: 1 }).run()} label="Heading 1">H1</Btn>
                <Btn onClick={() => editor.chain().focus().toggleHeading({ level: 2 }).run()} label="Heading 2">H2</Btn>
                <Btn onClick={() => editor.chain().focus().toggleBulletList().run()} label="Bulleted list">• List</Btn>
                <Btn onClick={() => editor.chain().focus().toggleOrderedList().run()} label="Numbered list">1. List</Btn>
                <Btn onClick={() => editor.chain().focus().toggleBlockquote().run()} label="Quote">&ldquo; Quote</Btn>
                <Btn onClick={() => editor.chain().focus().toggleCodeBlock().run()} label="Code block">Code</Btn>
                <Btn onClick={() => editor.chain().focus().setHorizontalRule().run()} label="Divider">— Divider</Btn>
                <Btn onClick={pickImage} label="Add photo">＋ Photo</Btn>
              </div>
            </FloatingMenu>
          </>
        )}

        <EditorContent editor={editor} />

        <input
          ref={fileInputRef}
          type="file"
          accept="image/png,image/jpeg,image/webp,image/gif"
          hidden
          onChange={onFilePicked}
        />
      </div>
    );
  },
);

/** One toolbar button (active state mirrors the editor's mark/node). */
function Btn({
  children,
  onClick,
  on,
  label,
}: {
  children: ReactNode;
  onClick: () => void;
  on?: boolean;
  label: string;
}) {
  return (
    <button
      type="button"
      className={`jmenu__btn${on ? " jmenu__btn--on" : ""}`}
      onMouseDown={(e) => e.preventDefault()} // keep the editor selection
      onClick={onClick}
      title={label}
      aria-label={label}
    >
      {children}
    </button>
  );
}

export type { Editor };
